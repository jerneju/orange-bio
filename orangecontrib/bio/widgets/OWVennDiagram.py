"""

"""
from __future__ import division

# Venn Diagram widget in orange-bioinformatics has been superseded by
# Venn Diagram in Orange (in Visualize category). However until there is
# a stable release of Orange (on which we can depend) we leave this widget
# in palace and only allow it's use if the Orange installation does not yet
# have the replacement.

try:
    from Orange.OrangeWidgets.Visualize import OWVennDiagram
except ImportError:
    pass
else:
    raise ImportError

import math
import unicodedata

from collections import namedtuple, defaultdict, OrderedDict, Counter
from itertools import count
from xml.sax.saxutils import escape

import numpy

from PyQt4.QtGui import (
    QComboBox, QGraphicsScene, QGraphicsView, QGraphicsWidget,
    QGraphicsPathItem, QGraphicsTextItem, QPainterPath, QPainter,
    QTransform, QColor, QBrush, QPen, QStyle, QPalette,
    QApplication
)

from PyQt4.QtCore import Qt, QPointF, QRectF, QLineF
from PyQt4.QtCore import pyqtSignal as Signal

import Orange

from Orange.OrangeWidgets.OWWidget import OWWidget, Multiple

from Orange.OrangeWidgets import OWGUI, OWItemModels, OWColorPalette

NAME = "Venn Diagram"

ICON = "icons/VennDiagram.svg"

INPUTS = [("Data", Orange.data.Table, "setData", Multiple)]

OUTPUTS = [("Data", Orange.data.Table)]


_InputData = namedtuple("_InputData", ["key", "name", "table"])
_ItemSet = namedtuple("_ItemSet", ["key", "name", "title", "items"])


class OWVennDiagram(OWWidget):
    settingsList = ["selection", "autocommit", "inputhints"]

    def __init__(self, parent=None, signalManager=None,
                 title="Venn Diagram"):
        super(OWVennDiagram, self).__init__(parent, signalManager, title,
                                            wantGraph=True)

        self.autocommit = False
        # Selected disjoint subset indices
        self.selection = []

        # Stored input set hints
        # {(index, inputname, attributes): (selectedattrname, itemsettitle)}
        # The 'selectedattrname' can be None
        self.inputhints = {}

        self.loadSettings()

        # Output changed flag
        self._changed = False
        # Diagram update is in progress
        self._updating = False
        # Input update is in progress
        self._inputUpdate = False

        # Input datasets
        self.data = OrderedDict()
        # Extracted input item sets
        self.itemsets = OrderedDict()

        # GUI
        box = OWGUI.widgetBox(self.controlArea, "Info")
        self.info = OWGUI.widgetLabel(box, "No data on input\n")

        self.inputsBox = OWGUI.widgetBox(self.controlArea, "Input data sets")

        for i in range(5):
            box = OWGUI.widgetBox(self.inputsBox, "Input %i" % i, flat=True)
            model = OWItemModels.VariableListModel(parent=self)
            cb = QComboBox()
            cb.setModel(model)
            cb.activated[int].connect(self._on_inputAttrActivated)
            box.setEnabled(False)
            # Store the combo in the box for later use.
            box.combo_box = cb
            box.layout().addWidget(cb)

        OWGUI.rubber(self.controlArea)

        box = OWGUI.widgetBox(self.controlArea, "Output")
        cb = OWGUI.checkBox(box, self, "autocommit", "Commit on any change")
        b = OWGUI.button(box, self, "Commit", callback=self.commit,
                         default=True)
        OWGUI.setStopper(self, b, cb, "_changed", callback=self.commit)

        # Main area view
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setBackgroundRole(QPalette.Window)
        self.view.setFrameStyle(QGraphicsView.StyledPanel)

        self.mainArea.layout().addWidget(self.view)
        self.vennwidget = VennDiagram()
        self.vennwidget.resize(400, 400)
        self.vennwidget.itemTextEdited.connect(self._on_itemTextEdited)
        self.scene.selectionChanged.connect(self._on_selectionChanged)

        self.scene.addItem(self.vennwidget)

        self.resize(self.controlArea.sizeHint().width() + 550,
                    max(self.controlArea.sizeHint().height(), 550))

        self._queue = []
        self.graphButton.clicked.connect(self.saveImage)

    def setData(self, data, key=None):
        self.error(0)
        if not self._inputUpdate:
            self._storeHints()
            self._inputUpdate = True

        if key in self.data:
            if data is None:
                # Remove the input
                self._remove(key)
            else:
                # Update existing item
                self._update(key, data)
        elif data is not None:
            # TODO: Allow setting more them 5 inputs and let the user
            # select the 5 to display.
            if len(self.data) == 5:
                self.error(0, "Can only take 5 inputs.")
                return
            # Add a new input
            self._add(key, data)

    def handleNewSignals(self):
        self._inputUpdate = False
        incremental = all(inc for _, inc in self._queue)

        if incremental:
            self._updateItemsets()
        else:
            self._createItemsets()
            self._restoreHints()
            self._updateItemsets()

        del self._queue[:]

        self._createDiagram()
        if self.data:
            self.info.setText(
                "{} data sets on input.\n".format(len(self.data)))
        else:
            self.info.setText("No data on input\n")

        OWWidget.handleNewSignals(self)

    def _invalidate(self, keys=None, incremental=True):
        """
        Invalidate input for a list of input keys.
        """
        if keys is None:
            keys = self.data.keys()

        self._queue.extend((key, incremental) for key in keys)

    def itemsetAttr(self, key):
        index = self.data.keys().index(key)
        _, combo = self._controlAtIndex(index)
        model = combo.model()
        attr_index = combo.currentIndex()
        if attr_index >= 0:
            return model[attr_index]
        else:
            return None

    def _controlAtIndex(self, index):
        group_box = self.inputsBox.layout().itemAt(index).widget()
        combo = group_box.combo_box
        return group_box, combo

    def _setAttributes(self, index, attrs):
        box, combo = self._controlAtIndex(index)
        model = combo.model()

        if attrs is None:
            model[:] = []
            box.setEnabled(False)
        else:
            if model[:] != attrs:
                model[:] = attrs

            box.setEnabled(True)

    def _add(self, key, table):
        name = table.name
        index = len(self.data)
        attrs = source_attributes(table.domain)
        if not attrs:
            self.warning(
                index, "Input {} has no suitable attributes.".format(index))

        self.data[key] = _InputData(key, name, table)

        self._setAttributes(index, attrs)

        self._invalidate([key], incremental=False)

        item = self.inputsBox.layout().itemAt(index)
        box = item.widget()
        box.setTitle("Input: {}".format(name))

    def _remove(self, key):
        index = self.data.keys().index(key)

        # Clear possible warnings.
        self.warning(index)

        self._setAttributes(index, None)

        del self.data[key]

        layout = self.inputsBox.layout()
        item = layout.takeAt(index)
        layout.addItem(item)
        inputs = self.data.values()

        for i in range(5):
            box, _ = self._controlAtIndex(i)
            if i < len(inputs):
                title = "Input: {}".format(inputs[i].name)
            else:
                title = "Input {}".format(i)
            box.setTitle(title)

        self._invalidate([key], incremental=False)

    def _update(self, key, table):
        name = table.name
        index = self.data.keys().index(key)
        attrs = source_attributes(table.domain)
        if not attrs:
            self.warning(
                index, "Input {} has no suitable attributes.".format(index))
        else:
            # Clear possible warnings.
            self.warning(index)
        self.data[key] = self.data[key]._replace(name=name, table=table)

        self._setAttributes(index, attrs)
        self._invalidate([key])

        item = self.inputsBox.layout().itemAt(index)
        box = item.widget()
        box.setTitle("Input: {}".format(name))

    def _updateItemsets(self):
        assert self.data.keys() == self.itemsets.keys()
        for key, input in self.data.items():
            attr = self.itemsetAttr(key)
            if attr is not None:
                items = [str(inst[attr]) for inst in input.table
                         if not inst[attr].is_special()]
            else:
                items = []

            item = self.itemsets[key]
            item = item._replace(items=items)
            if item.name != input.name:
                item = item._replace(name=input.name, title=input.name)
            self.itemsets[key] = item

    def _createItemsets(self):
        olditemsets = dict(self.itemsets)
        self.itemsets.clear()
        for key, input in self.data.items():
            attr = self.itemsetAttr(key)
            if attr is not None:
                items = [str(inst[attr]) for inst in input.table
                         if not inst[attr].is_special()]
            else:
                items = []

            title = input.name
            if key in olditemsets and olditemsets[key].name == input.name:
                # Reuse the title (which might have been changed by the user)
                title = olditemsets[key].title

            itemset = _ItemSet(key=key, name=input.name, title=title,
                               items=items)
            self.itemsets[key] = itemset

    def _storeHints(self):
        if self.data:
            self.inputhints.clear()
            for i, (key, input) in enumerate(self.data.items()):
                attrs = source_attributes(input.table.domain)
                attrs = tuple(attr.name for attr in attrs)
                selected = self.itemsetAttr(key)
                if selected is not None:
                    attr_name = selected.name
                else:
                    attr_name = None
                itemset = self.itemsets[key]
                self.inputhints[(i, input.name, attrs)] = \
                    (attr_name, itemset.title)

    def _restoreHints(self):
        settings = []
        for i, (key, input) in enumerate(self.data.items()):
            attrs = source_attributes(input.table.domain)
            attrs = tuple(attr.name for attr in attrs)
            hint = self.inputhints.get((i, input.name, attrs), None)
            if hint is not None:
                attr, name = hint
                attr_ind = attrs.index(attr) if attr is not None else -1
                settings.append((attr_ind, name))
            else:
                return

        # all inputs match the stored hints
        for i, key in enumerate(self.itemsets):
            attr, itemtitle = settings[i]
            self.itemsets[key] = self.itemsets[key]._replace(title=itemtitle)
            _, cb = self._controlAtIndex(i)
            cb.setCurrentIndex(attr)

    def _createDiagram(self):
        self._updating = True

        oldselection = list(self.selection)

        self.vennwidget.clear()
        n = len(self.itemsets)
        self.disjoint = disjoint(set(s.items) for s in self.itemsets.values())

        vennitems = []
        colors = OWColorPalette.ColorPaletteHSV(n)

        for i, (key, item) in enumerate(self.itemsets.items()):
            gr = VennSetItem(text=item.title, count=len(item.items))
            color = colors[i]
            color.setAlpha(100)
            gr.setBrush(QBrush(color))
            gr.setPen(QPen(Qt.NoPen))
            vennitems.append(gr)

        self.vennwidget.setItems(vennitems)

        for i, area in enumerate(self.vennwidget.vennareas()):
            area_items = list(self.disjoint[i])
            if i:
                area.setText("{0}".format(len(area_items)))

            label = disjoint_set_label(i, n, simplify=False)
            head = u"<h4>|{}| = {}</h4>".format(label, len(area_items))
            if len(area_items) > 32:
                items_str = u", ".join(map(escape, area_items[:32]))
                hidden = len(area_items) - 32
                tooltip = (u"{}<span>{}, ...</br>({} items not shown)<span>"
                           .format(head, items_str, hidden))
            elif area_items:
                tooltip = u"{}<span>{}</span>".format(
                    head,
                    u", ".join(map(escape, area_items))
                )
            else:
                tooltip = head

            area.setToolTip(tooltip)

            area.setPen(QPen(QColor(10, 10, 10, 200), 1.5))
            area.setFlag(QGraphicsPathItem.ItemIsSelectable, True)
            area.setSelected(i in oldselection)

        self._updating = False
        self._on_selectionChanged()

    def _on_selectionChanged(self):
        if self._updating:
            return

        areas = self.vennwidget.vennareas()
        indices = [i for i, area in enumerate(areas)
                   if area.isSelected()]

        self.selection = indices

        self.invalidateOutput()

    def _on_inputAttrActivated(self, attr_index):
        combo = self.sender()
        # Find the input index to which the combo box belongs
        # (they are reordered when removing inputs).
        index = None
        inputs = self.data.items()
        for i in range(len(inputs)):
            _, c = self._controlAtIndex(i)
            if c is combo:
                index = i
                break

        assert (index is not None)

        key, _ = inputs[index]

        self._invalidate([key])
        self._updateItemsets()
        self._createDiagram()

    def _on_itemTextEdited(self, index, text):
        text = str(text)
        key = self.itemsets.keys()[index]
        self.itemsets[key] = self.itemsets[key]._replace(title=text)

    def invalidateOutput(self):
        if self.autocommit:
            self.commit()
        else:
            self._changed = True

    def commit(self):
        selected_subsets = []

        selected_items = reduce(
            set.union, [self.disjoint[index] for index in self.selection],
            set()
        )

        def match(val):
            if val.is_special():
                return False
            else:
                return str(val) in selected_items

        source_mid = Orange.core.newmetaid()
        source_var = Orange.feature.String("source")
        item_mid = Orange.core.newmetaid()
        item_id_var = Orange.feature.String("item_id")

        names = [itemset.title.strip() for itemset in self.itemsets.values()]
        names = uniquify(names)

        for i, (key, input) in enumerate(self.data.items()):
            attr = self.itemsetAttr(key)
            if attr is not None:
                mask = map(match, (inst[attr] for inst in input.table))
            else:
                mask = [False] * len(input.table)

            subset = Orange.data.Table(input.table.domain.clone(),
                                       input.table.select(mask))

            assert subset.domain is not input.table.domain

            # add a column with source table id to the data
            subset.domain.add_meta(source_mid, source_var)
            subset.add_meta_attribute(source_var, str(names[i]))
            # add a column with instance set id
            subset.domain.add_meta(item_mid, item_id_var)
            subset.add_meta_attribute(item_mid)
            for inst in subset:
                inst[item_id_var] = str(inst[attr])

            if subset:
                selected_subsets.append(subset)

        if selected_subsets:
            data = table_concat(selected_subsets)
            # Get all variables which are not constant between the same
            # item set
            varying = varying_between(data, [item_id_var])
            if source_var in varying:
                varying.remove(source_var)

            data = reshape_wide(data, varying, [item_id_var], [source_var])
            # remove the temporary item set id column
            data.remove_meta_attribute(item_id_var)
            data.domain.remove_meta(item_id_var)
        else:
            data = None

        self.send("Data", data)

    def saveImage(self):
        from Orange.OrangeWidgets.OWDlgs import OWChooseImageSizeDlg
        dlg = OWChooseImageSizeDlg(self.scene, parent=self)
        dlg.exec_()

    def getSettings(self, *args, **kwargs):
        self._storeHints()
        return OWWidget.getSettings(self, *args, **kwargs)


def table_concat(tables):
    """
    Concatenate a list of tables.

    The resulting table will have a union of all attributes of `tables`.

    """
    features = []
    features_seen = set()
    class_var = None
    metas = {}
    metas_seen = set()

    for table in tables:
        features.extend(f for f in table.domain.features
                        if f not in features_seen)
        features_seen.update(features)

        new_metas = {}

        if table.domain.class_var is not None:
            if class_var is not None and table.domain.class_var != class_var:
                # Move the extra class variables to meta attributes
                if table.domain.class_var not in metas_seen:
                    new_metas[Orange.core.newmetaid()] = table.domain.class_var
                    metas_seen.add(table.domain.class_var)
            else:
                class_var = table.domain.class_var
                features_seen.add(class_var)

        new_metas.update(
            {mid: meta for mid, meta in table.domain.getmetas().items()
             if meta not in metas_seen}
        )

        metas_seen.update(new_metas.itervalues())

        metas.update(new_metas)

    domain = Orange.data.Domain(features, class_var)
    domain.addmetas(metas)

    new_table = Orange.data.Table(domain)
    for table in tables:
        new_table.extend(table.translate(domain))

    return new_table


def copy_descriptor(descriptor, newname=None):
    """
    Create a copy of the descriptor.

    If newname is not None the new descriptor will have the same
    name as the input.

    """
    if newname is None:
        newname = descriptor.name

    if isinstance(descriptor, Orange.feature.Discrete):
        newf = Orange.feature.Discrete(
            newname,
            values=descriptor.values,
            base_value=descriptor.base_value,
            ordered=descriptor.ordered,
            attributes=dict(descriptor.attributes))

    elif isinstance(descriptor, Orange.feature.Continuous):
        newf = Orange.feature.Continuous(
            newname,
            number_of_decimals=descriptor.number_of_decimals,
            scientific_format=descriptor.scientific_format,
            adjust_decimals=descriptor.adjust_decimals,
            attributes=dict(descriptor.attributes))

    else:
        newf = type(descriptor)(
            newname,
            attributes=dict(descriptor.attributes))

    return newf


def reshape_wide(table, varlist, idvarlist, groupvarlist):
    """
    Reshape a data table into a wide format.

    :param Orange.data.Table table:
        Source data table in long format.
    :param varlist:
        A list of variables to reshape.
    :param list idvarlist:
        A list of variables in `table` uniquely identifying multiple
        records in `table` (i.e. subject id).
    :param groupvarlist:
        A list of variables differentiating multiple records
        (i.e. conditions).

    """
    def inst_key(inst, vars):
        return tuple(str(inst[var]) for var in vars)

    instance_groups = [inst_key(inst, groupvarlist) for inst in table]
    # A list of groups (for each element in a group the varying variable
    # will be duplicated)
    groups = list(unique(instance_groups))
    group_names = [", ".join(group) for group in groups]

    # A list of instance ids (subject ids)
    # Each instance in the output will correspond to one of these ids)
    instance_ids = [inst_key(inst, idvarlist) for inst in table]
    ids = list(unique(instance_ids))

    # an mapping from ids to an list of input instance indices
    # each instance in this list belongs to one group (but not all
    # groups need to be present).
    inst_by_id = defaultdict(list)

    for i, inst in enumerate(table):
        inst_id = instance_ids[i]
        inst_by_id[inst_id].append(i)

    newfeatures = []
    newmetas = []
    expanded_features = {}

    def expanded(feat):
        return [copy_descriptor(feat, newname="%s (%s)" %
                                (feat.name, group_name))
                for group_name in group_names]

    for feat in table.domain.variables:
        if feat in varlist:
            features = expanded(feat)
            newfeatures.extend(features)
            expanded_features[feat] = dict(zip(groups, features))
        elif feat not in groupvarlist:
            newfeatures.append(feat)

    for mid, meta in table.domain.getmetas().items():
        if meta in varlist:
            metas = expanded(meta)
            newmetas.extend((Orange.core.newmetaid(), meta)
                            for meta in metas)
            expanded_features[meta] = dict(zip(groups, metas))
        elif meta not in groupvarlist:
            newmetas.append((mid, meta))

    if table.domain.class_var and table.domain.class_var is newfeatures[-1]:
        new_class_var = table.domain.class_var
        newfeatures = newfeatures[:-1]
    else:
        new_class_var = None

    domain = Orange.data.Domain(newfeatures, new_class_var)
    domain.addmetas(dict(newmetas))

    newtable = Orange.data.Table(domain)

    for inst_id in ids:
        indices = inst_by_id[inst_id]
        inst = Orange.data.Instance(domain, table[indices[0]])
        for index in indices:
            source_inst = table[index]
            group = instance_groups[index]
            for source_var in varlist:
                newf = expanded_features[source_var][group]
                inst[newf] = source_inst[source_var].value
        newtable.append(inst)

    return newtable


def unique(seq):
    """
    Return an iterator over unique items of `seq`.

    .. note:: Items must be hashable.

    """
    seen = set()
    for item in seq:
        if item not in seen:
            yield item
            seen.add(item)


def varying_between(table, idvarlist):
    """
    Return a list of all variables with non constant values between
    groups defined by `idvarlist`.

    """
    def inst_key(inst, vars):
        return tuple(str(inst[var]) for var in vars)

    excluded = set(idvarlist)
    all_possible = [var for var in (table.domain.variables +
                                    table.domain.getmetas().values())
                    if var not in excluded]
    candidate_set = set(all_possible)

    idmap = Orange.data.utils.table_map(table, idvarlist)
    values = {}
    varying = set()
    for indices in idmap.values():
        for var in list(candidate_set):
            values = [table[i][var].native() for i in indices]
            if len(set(values)) != 1:
                varying.add(var)
                candidate_set.remove(var)

    return sorted(varying, key=all_possible.index)


def uniquify(strings):
    """
    Return a list of unique strings.

    The string at i'th position will have the same prefix as strings[i]
    with an appended suffix to make the item unique (if necessary).

    >>> uniquify(["cat", "dog", "cat"])
    ["cat 1", "dog", "cat 2"]

    """
    counter = Counter(strings)
    counts = defaultdict(count)
    newstrings = []
    for string in strings:
        if counter[string] > 1:
            newstrings.append(string + (" %i" % (next(counts[string]) + 1)))
        else:
            newstrings.append(string)

    return newstrings


def string_attributes(domain):
    """
    Return all string attributes from the domain.
    """
    return [attr for attr in domain.variables +
                domain.getmetas().values()
            if isinstance(attr, Orange.feature.String)]


def discrete_attributes(domain):
    """
    Return all discrete attributes from the domain.
    """
    return filter(lambda attr: isinstance(attr, Orange.feature.Discrete),
                  domain.variables + domain.getmetas().values())


def source_attributes(domain):
    """
    Return all suitable attributes for the venn diagram.
    """
    return string_attributes(domain) + discrete_attributes(domain)


def disjoint(sets):
    """
    Return all disjoint subsets.
    """
    sets = list(sets)
    n = len(sets)
    disjoint_sets = [None] * (2 ** n)
    for i in range(2 ** n):
        key = setkey(i, n)
        included = [s for s, inc in zip(sets, key) if inc]
        excluded = [s for s, inc in zip(sets, key) if not inc]
        if any(included):
            s = reduce(set.intersection, included)
        else:
            s = set()

        s = reduce(set.difference, excluded, s)

        disjoint_sets[i] = s

    return disjoint_sets


def disjoint_set_label(i, n, simplify=False):
    """
    Return a html formated label for a disjoint set indexed by `i`.
    """
    intersection = unicodedata.lookup("INTERSECTION")
    # comp = unicodedata.lookup("COMPLEMENT")  #
    # This depends on the font but the unicode complement in
    # general does not look nice in a super script so we use
    # plain c instead.
    comp = "c"

    def label_for_index(i):
        return chr(ord("A") + i)

    if simplify:
        return "".join(label_for_index(i) for i, b in enumerate(setkey(i, n))
                       if b)
    else:
        return intersection.join(label_for_index(i) +
                                 ("" if b else "<sup>" + comp + "</sup>")
                                 for i, b in enumerate(setkey(i, n)))


class VennSetItem(QGraphicsPathItem):
    def __init__(self, parent=None, text=None, count=None):
        super(VennSetItem, self).__init__(parent)
        self.text = text
        self.count = count


# TODO: Use palette's selected/highligted text / background colors to
# indicate selection

class VennIntersectionArea(QGraphicsPathItem):
    def __init__(self, parent=None, text=""):
        super(QGraphicsPathItem, self).__init__(parent)
        self.setAcceptHoverEvents(True)
        self.setPen(QPen(Qt.NoPen))

        self.text = QGraphicsTextItem(self)
        layout = self.text.document().documentLayout()
        layout.documentSizeChanged.connect(self._onLayoutChanged)

        self._text = ""
        self._anchor = QPointF()

    def setText(self, text):
        if self._text != text:
            self._text = text
            self.text.setPlainText(text)

    def text(self):
        return self._text

    def setTextAnchor(self, pos):
        if self._anchor != pos:
            self._anchor = pos
            self._updateTextAnchor()

    def hoverEnterEvent(self, event):
        self.setZValue(self.zValue() + 1)
        return QGraphicsPathItem.hoverEnterEvent(self, event)

    def hoverLeaveEvent(self, event):
        self.setZValue(self.zValue() - 1)
        return QGraphicsPathItem.hoverLeaveEvent(self, event)

#     def mousePressEvent(self, event):
#         pos = event.pos()
#         parent = self.parentItem()
#         pbrect = parent.boundingRect()
#         w, h = pbrect.width(), pbrect.height()
#         print "(%.3f, %.3f)" % (pos.x() / w, pos.y() / h)
#         super(VennIntersectionArea, self).mousePressEvent(event)

    def paint(self, painter, option, widget=None):
        painter.save()
        path = self.path()
        brush = QBrush(self.brush())
        pen = QPen(self.pen())

        if option.state & QStyle.State_Selected:
            pen.setColor(Qt.red)
            brush.setStyle(Qt.DiagCrossPattern)
            brush.setColor(QColor(40, 40, 40, 100))

        elif option.state & QStyle.State_MouseOver:
            pen.setColor(Qt.blue)

        if option.state & QStyle.State_MouseOver:
            brush.setColor(QColor(100, 100, 100, 100))
            if brush.style() == Qt.NoBrush:
                # Make sure the highlight is actually visible.
                brush.setStyle(Qt.SolidPattern)

        painter.setPen(pen)
        painter.setBrush(brush)
        painter.drawPath(path)
        painter.restore()

    def itemChange(self, change, value):
        if change == QGraphicsPathItem.ItemSelectedHasChanged:
            if value.toBool():
                self.setZValue(self.zValue() + 1)
            else:
                self.setZValue(self.zValue() - 1)

        return QGraphicsPathItem.itemChange(self, change, value)

    def _updateTextAnchor(self):
        rect = self.text.boundingRect()
        pos = anchor_rect(rect, self._anchor)
        self.text.setPos(pos)

    def _onLayoutChanged(self):
        self._updateTextAnchor()


class GraphicsTextEdit(QGraphicsTextItem):
    #: Edit triggers
    NoEditTriggers, DoubleClicked = 0, 1

    editingFinished = Signal()
    editingStarted = Signal()

    documentSizeChanged = Signal()

    def __init__(self, *args, **kwargs):
        super(GraphicsTextEdit, self).__init__(*args, **kwargs)
        self.setTabChangesFocus(True)
        self._edittrigger = GraphicsTextEdit.DoubleClicked
        self._editing = False
        self.document().documentLayout().documentSizeChanged.connect(
            self.documentSizeChanged
        )

    def mouseDoubleClickEvent(self, event):
        super(GraphicsTextEdit, self).mouseDoubleClickEvent(event)
        if self._edittrigger == GraphicsTextEdit.DoubleClicked:
            self._start()

    def focusOutEvent(self, event):
        super(GraphicsTextEdit, self).focusOutEvent(event)

        if self._editing:
            self._end()

    def _start(self):
        self._editing = True
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus(Qt.MouseFocusReason)
        self.editingStarted.emit()

    def _end(self):
        self._editing = False
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.editingFinished.emit()


class VennDiagram(QGraphicsWidget):
    # rect and petal are for future work
    Circle, Ellipse, Rect, Petal = 1, 2, 3, 4

    TitleFormat = "<center><h4>{0}</h4>{1}</center>"

    selectionChanged = Signal()
    itemTextEdited = Signal(int, str)

    def __init__(self, parent=None):
        super(VennDiagram, self).__init__(parent)
        self.shapeType = VennDiagram.Circle

        self._setup()

    def _setup(self):
        self._items = []
        self._vennareas = []
        self._textitems = []

    def item(self, index):
        return self._items[index]

    def items(self):
        return list(self._items)

    def count(self):
        return len(self._items)

    def setItems(self, items):
        if self._items:
            self.clear()

        self._items = list(items)

        for item in self._items:
            item.setParentItem(self)
            item.setVisible(True)

        fmt = self.TitleFormat.format

        font = self.font()
        font.setPixelSize(14)

        for item in items:
            text = GraphicsTextEdit(self)
            text.setFont(font)
            text.setDefaultTextColor(QColor("#333"))
            text.setHtml(fmt(escape(item.text), item.count))
            text.adjustSize()
            text.editingStarted.connect(self._on_editingStarted)
            text.editingFinished.connect(self._on_editingFinished)
            text.documentSizeChanged.connect(
                self._on_itemTextSizeChanged
            )

            self._textitems.append(text)

        self._vennareas = [
            VennIntersectionArea(parent=self)
            for i in range(2 ** len(items))
        ]
        self._subsettextitems = [
            QGraphicsTextItem(parent=self)
            for i in range(2 ** len(items))
        ]

        self._updateLayout()

    def clear(self):
        scene = self.scene()
        items = self.vennareas() + self.items() + self._textitems

        for item in self._textitems:
            item.editingStarted.disconnect(self._on_editingStarted)
            item.editingFinished.disconnect(self._on_editingFinished)
            item.documentSizeChanged.disconnect(
                self._on_itemTextSizeChanged
            )

        self._items = []
        self._vennareas = []
        self._textitems = []

        for item in items:
            item.setVisible(False)
            item.setParentItem(None)
            if scene is not None:
                scene.removeItem(item)

    def vennareas(self):
        return list(self._vennareas)

    def setFont(self, font):
        if self._font != font:
            self.prepareGeometryChange()
            self._font = font

            for item in self.items():
                item.setFont(font)

    def _updateLayout(self):
        rect = self.geometry()
        n = len(self._items)
        if not n:
            return

        regions = venn_diagram(n, shape=self.shapeType)

        # The y axis in Qt points downward
        transform = QTransform().scale(1, -1)
        regions = map(transform.map, regions)

        union_brect = reduce(QRectF.united,
                             (path.boundingRect() for path in regions))

        scalex = rect.width() / union_brect.width()
        scaley = rect.height() / union_brect.height()
        scale = min(scalex, scaley)

        transform = QTransform().scale(scale, scale)

        regions = [transform.map(path) for path in regions]

        center = rect.width() / 2, rect.height() / 2
        for item, path in zip(self.items(), regions):
            item.setPath(path)
            item.setPos(*center)

        intersections = venn_intersections(regions)
        assert len(intersections) == 2 ** n
        assert len(self.vennareas()) == 2 ** n

        anchors = [(0, 0)] + subset_anchors(self._items)

        anchor_transform = QTransform().scale(rect.width(), -rect.height())
        for i, area in enumerate(self.vennareas()):
            area.setPath(intersections[setkey(i, n)])
            area.setPos(*center)
            x, y = anchors[i]
            anchor = anchor_transform.map(QPointF(x, y))
            area.setTextAnchor(anchor)
            area.setZValue(30)

        self._updateTextAnchors()

    def _updateTextAnchors(self):
        n = len(self._items)

        items = self._items
        dist = 15

        shape = reduce(QPainterPath.united, [item.path() for item in items])
        brect = shape.boundingRect()
        bradius = max(brect.width() / 2, brect.height() / 2)

        center = self.boundingRect().center()

        anchors = _category_anchors(items)
        self._textanchors = []
        for angle, anchor_h, anchor_v in anchors:
            line = QLineF.fromPolar(bradius, angle)
            ext = QLineF.fromPolar(dist, angle)
            line = QLineF(line.p1(), line.p2() + ext.p2())
            line = line.translated(center)

            anchor_pos = line.p2()
            self._textanchors.append((anchor_pos, anchor_h, anchor_v))

        for i in range(n):
            self._updateTextItemPos(i)

    def _updateTextItemPos(self, i):
        item = self._textitems[i]
        anchor_pos, anchor_h, anchor_v = self._textanchors[i]
        rect = item.boundingRect()
        pos = anchor_rect(rect, anchor_pos, anchor_h, anchor_v)
        item.setPos(pos)

    def setGeometry(self, geometry):
        super(VennDiagram, self).setGeometry(geometry)
        self._updateLayout()

    def paint(self, painter, option, w):
        super(VennDiagram, self).paint(painter, option, w)
#         painter.drawRect(self.boundingRect())

    def _on_editingStarted(self):
        item = self.sender()
        index = self._textitems.index(item)
        text = self._items[index].text
        item.setTextWidth(-1)
        item.setHtml(self.TitleFormat.format(escape(text), "<br/>"))

    def _on_editingFinished(self):
        item = self.sender()
        index = self._textitems.index(item)
        text = item.toPlainText()
        if text != self._items[index].text:
            self._items[index].text = text

            self.itemTextEdited.emit(index, text)

        item.setHtml(
            self.TitleFormat.format(escape(text), self._items[index].count))
        item.adjustSize()

    def _on_itemTextSizeChanged(self):
        item = self.sender()
        index = self._textitems.index(item)
        self._updateTextItemPos(index)


def anchor_rect(rect, anchor_pos,
                anchor_h=Qt.AnchorHorizontalCenter,
                anchor_v=Qt.AnchorVerticalCenter):

    if anchor_h == Qt.AnchorLeft:
        x = anchor_pos.x()
    elif anchor_h == Qt.AnchorHorizontalCenter:
        x = anchor_pos.x() - rect.width() / 2
    elif anchor_h == Qt.AnchorRight:
        x = anchor_pos.x() - rect.width()
    else:
        raise ValueError(anchor_h)

    if anchor_v == Qt.AnchorTop:
        y = anchor_pos.y()
    elif anchor_v == Qt.AnchorVerticalCenter:
        y = anchor_pos.y() - rect.height() / 2
    elif anchor_v == Qt.AnchorBottom:
        y = anchor_pos.y() - rect.height()
    else:
        raise ValueError(anchor_v)

    return QPointF(x, y)


def radians(angle):
    return 2 * math.pi * angle / 360


def unit_point(x, r=1.0):
    x = radians(x)
    return (r * math.cos(x), r * math.sin(x))


def _category_anchors(shapes):
    n = len(shapes)
    return _CATEGORY_ANCHORS[n - 1]


# (angle, horizontal anchor, vertical anchor)
_CATEGORY_ANCHORS = (
    # n == 1
    ((90, Qt.AnchorHorizontalCenter, Qt.AnchorBottom),),
    # n == 2
    ((180, Qt.AnchorRight, Qt.AnchorVerticalCenter),
     (0, Qt.AnchorLeft, Qt.AnchorVerticalCenter)),
    # n == 3
    ((150, Qt.AnchorRight, Qt.AnchorBottom),
     (30, Qt.AnchorLeft, Qt.AnchorBottom),
     (270, Qt.AnchorHorizontalCenter, Qt.AnchorTop)),
    # n == 4
    ((270 + 45, Qt.AnchorLeft, Qt.AnchorTop),
     (270 - 45, Qt.AnchorRight, Qt.AnchorTop),
     (90 - 15, Qt.AnchorLeft, Qt.AnchorBottom),
     (90 + 15, Qt.AnchorRight, Qt.AnchorBottom)),
    # n == 5
    ((90 - 5, Qt.AnchorHorizontalCenter, Qt.AnchorBottom),
     (18 - 5, Qt.AnchorLeft, Qt.AnchorVerticalCenter),
     (306 - 5, Qt.AnchorLeft, Qt.AnchorTop),
     (234 - 5, Qt.AnchorRight, Qt.AnchorTop),
     (162 - 5, Qt.AnchorRight, Qt.AnchorVerticalCenter),)
)


def subset_anchors(shapes):
    n = len(shapes)
    if n == 1:
        return [(0, 0)]
    elif n == 2:
        return [unit_point(180, r=1/3),
                unit_point(0, r=1/3),
                (0, 0)]
    elif n == 3:
        return [unit_point(150, r=0.35),  # A
                unit_point(30, r=0.35),   # B
                unit_point(90, r=0.27),   # AB
                unit_point(270, r=0.35),  # C
                unit_point(210, r=0.27),  # AC
                unit_point(330, r=0.27),  # BC
                unit_point(0, r=0),       # ABC
                ]
    elif n == 4:
        anchors = [
            (0.400, 0.110),    # A
            (-0.400, 0.110),   # B
            (0.000, -0.285),   # AB
            (0.180, 0.330),    # C
            (0.265, 0.205),    # AC
            (-0.240, -0.110),  # BC
            (-0.100, -0.190),  # ABC
            (-0.180, 0.330),   # D
            (0.240, -0.110),   # AD
            (-0.265, 0.205),   # BD
            (0.100, -0.190),   # ABD
            (0.000, 0.250),    # CD
            (0.153, 0.090),    # ACD
            (-0.153, 0.090),   # BCD
            (0.000, -0.060),   # ABCD
        ]
        return anchors

    elif n == 5:
        anchors = [None] * 32
        # Base anchors
        A = (0.033, 0.385)
        AD = (0.095, 0.250)
        AE = (-0.100, 0.265)
        ACE = (-0.130, 0.220)
        ADE = (0.010, 0.225)
        ACDE = (-0.095, 0.175)
        ABCDE = (0.0, 0.0)

        anchors[-1] = ABCDE

        bases = [(0b00001, A),
                 (0b01001, AD),
                 (0b10001, AE),
                 (0b10101, ACE),
                 (0b11001, ADE),
                 (0b11101, ACDE)]

        for i in range(5):
            for index, anchor in bases:
                index = bit_rot_left(index, i, bits=5)
                assert anchors[index] is None
                anchors[index] = rotate_point(anchor, - 72 * i)

        assert all(anchors[1:])
        return anchors[1:]


def bit_rot_left(x, y, bits=32):
    mask = 2 ** bits - 1
    x_masked = x & mask
    return (x << y) & mask | (x_masked >> bits - y)


def rotate_point(p, angle):
    r = radians(angle)
    R = numpy.array([[math.cos(r), -math.sin(r)],
                     [math.sin(r), math.cos(r)]])
    x, y = numpy.dot(R, p)
    return (float(x), float(y))


def line_extended(line, distance):
    """
    Return an QLineF extended by `distance` units in the positive direction.
    """
    angle = line.angle() / 360 * 2 * math.pi
    dx, dy = unit_point(angle, r=distance)
    return QLineF(line.p1(), line.p2() + QPointF(dx, dy))


def circle_path(center, r=1.0):
    return ellipse_path(center, r, r, rotation=0)


def ellipse_path(center, a, b, rotation=0):
    if not isinstance(center, QPointF):
        center = QPointF(*center)

    brect = QRectF(-a, -b, 2 * a, 2 * b)

    path = QPainterPath()
    path.addEllipse(brect)

    if rotation != 0:
        transform = QTransform().rotate(rotation)
        path = transform.map(path)

    path.translate(center)
    return path


# TODO: Should include anchors for text layout (both inside and outside).
# for each item {path: QPainterPath,
#                text_anchors: [{center}] * (2 ** n)
#                mayor_axis: QLineF,
#                boundingRect QPolygonF (with 4 vertices)}
#
# Should be a new class with overloads for ellipse/circle, rect, and petal
# shapes, should store all constructor parameters, rotation, center,
# mayor/minor axis.


def venn_diagram(n, shape=VennDiagram.Circle):
    if n < 1 or n > 5:
        raise ValueError()

    paths = []

    if n == 1:
        paths = [circle_path(center=(0, 0), r=0.5)]
    elif n == 2:
        angles = [180, 0]
        paths = [circle_path(center=unit_point(x, r=1/6), r=1/3)
                 for x in angles]
    elif n == 3:
        angles = [150 - 120 * i for i in range(3)]
        paths = [circle_path(center=unit_point(x, r=1/6), r=1/3)
                 for x in angles]
    elif n == 4:
        # Constants shamelessly stolen from VennDiagram R package
        paths = [
            ellipse_path((0.65 - 0.5, 0.47 - 0.5), 0.35, 0.20, 45),
            ellipse_path((0.35 - 0.5, 0.47 - 0.5), 0.35, 0.20, 135),
            ellipse_path((0.5 - 0.5, 0.57 - 0.5), 0.35, 0.20, 45),
            ellipse_path((0.5 - 0.5, 0.57 - 0.5), 0.35, 0.20, 134),
        ]
    elif n == 5:
        # Constants shamelessly stolen from VennDiagram R package
        d = 0.13
        a, b = 0.24, 0.48
        a, b = b, a
        a, b = 0.48, 0.24
        paths = [ellipse_path(unit_point((1 - i) * 72, r=d),
                              a, b, rotation=90 - (i * 72))
                 for i in range(5)]

    return paths


def setkey(intval, n):
    return tuple(bool(intval & (2 ** i)) for i in range(n))


def keyrange(n):
    if n < 0:
        raise ValueError()

    for i in range(2 ** n):
        yield setkey(i, n)


def venn_intersections(paths):
    n = len(paths)
    return {key: venn_intersection(paths, key) for key in keyrange(n)}


def venn_intersection(paths, key):
    if not any(key):
        return QPainterPath()

    # first take the intersection of all included paths
    path = reduce(QPainterPath.intersected,
                  (path for path, included in zip(paths, key) if included))

    # subtract all the excluded sets (i.e. take the intersection
    # with the excluded set complements)
    path = reduce(QPainterPath.subtracted,
                  (path for path, included in zip(paths, key) if not included),
                  path)

    return path


def test():
    app = QApplication([])
    w = OWVennDiagram()
    data = Orange.data.Table("brown-selected")

    data.domain.addmetas({-42: Orange.feature.String("Test")})
    for i, inst in enumerate(data):
        inst[-42] = "{}".format(i % 30)

    random = Orange.misc.Random()
    indices = Orange.data.sample.SubsetIndices2(
        p0=0.7, random_generator=random)

    d1 = data.select(indices(data))
    d2 = data.select(indices(data))
    d3 = data.select(indices(data))
    d4 = data.select(indices(data))
    d5 = data.select(indices(data))

    n = 5
    for i, data in zip(range(n), [d1, d2, d3, d4, d5]):
        data.name = chr(ord("A") + i)
        w.setData(data, key=i)

    w.handleNewSignals()
    w.show()
    app.exec_()

    del w
    app.processEvents()
    return app


def test1():
    app = QApplication([])
    w = OWVennDiagram()
    data1 = Orange.data.Table("brown-selected")
    data2 = Orange.data.Table("brown-selected")
    w.setData(data1, 1)
    w.setData(data2, 2)
    w.handleNewSignals()

    w.show()
    app.exec_()

    del w
    return app

if __name__ == "__main__":
    test()
