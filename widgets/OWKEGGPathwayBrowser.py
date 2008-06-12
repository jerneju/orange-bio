"""
<name>KEGG Pathway browser</name>
<description>Browser that - given a set of genes - searches and displays relevant KEGG pathways</description>
<priority>220</priority>
<icon>icons/KEGG.png</icon>
"""

import sys
import orange
import obiKEGG

from OWWidget import *
from qt import *

import OWGUI

from collections import defaultdict

def split_and_strip(string, sep=None):
    return [s.strip() for s in string.split(sep)]

class PathwayToolTip(QToolTip):
    def __init__(self, parent):
        QToolTip.__init__(self, parent)
        self.parent = parent

    def maybeTip(self, p):
        objs = [(id, bb) for id, bb in  self.parent.GetObjects(p.x() ,p.y()) if id in self.parent.objects]
        if objs:
            genes = map(self.parent.master.uniqueGenesDict.get, dict(objs).keys())
            text = "<br>".join(genes)
            self.tip(QRect(p.x()-2, p.y()-2, 4, 4), text)

class PathwayView(QScrollView):
    def __init__(self, master, *args):
        QScrollView.__init__(self, *args)
        self.master = master
        self.toolTip = PathwayToolTip(self)
        self.setHScrollBarMode(QScrollView.Auto)
        self.setVScrollBarMode(QScrollView.Auto)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.bbDict = {}
        self.pixmap = None
        self.image = None
        self.popup = QPopupMenu()
        self.popup.insertItem("View genes on KEGG website", 0, 0)
        self.popup.insertItem("View pathway on KEGG website", 1, 1)
        self.popup.insertItem("View linked pathway", 2, 2)
        self.connect(self.popup, SIGNAL("activated ( int ) "), self.PopupAction)
        
    def SetPathway(self, pathway=None, objects=[]):
        self.pathway = pathway
        self.objects = objects
        if pathway:
            pathway.api.download_progress_callback = self.master.progressBarSet
            self.master.progressBarInit()
            self.image = image = self.pathway.get_image()
            self.bbDict = self.pathway.get_bounding_box_dict()
            self.master.progressBarFinished()
            self.ShowImage()
##            image.save(self.pathway.local_database_path+"TmpPathwayImage.png")
##            self.pixmap = QPixmap(obiKEGG.default_database_path+"TmpPathwayImage.png")
##            w, h = image.size
##            self.resizeContents(w, h)
##            self.updateContents(self.contentsX(), self.contentsY() ,self.viewport().width(), self.viewport().height())
        else:
            self.bbDict = {}
            self.pixmap = None
            self.resizeContents(0,0)

    def ShowImage(self):
        if self.master.autoResize:
            import Image
            w, h = self.image.size
            self.resizeFactor = factor = min(self.viewport().width()/float(w), self.viewport().height()/float(h))
            image = self.image.resize((int(w*factor), int(h*factor)), Image.ANTIALIAS)
        else:
            image = self.image
            self.resizeFactor = 1
        image.save(self.pathway.local_database_path+"TmpPathwayImage.png")
        self.pixmap = QPixmap(self.pathway.local_database_path+"TmpPathwayImage.png")
        w, h = image.size
        self.resizeContents(w, h)
        self.updateContents(self.contentsX(), self.contentsY() ,self.viewport().width(), self.viewport().height())

    def drawContents(self, painter, cx=0, cy=0, cw=-1, ch=-1):
        QScrollView.drawContents(self, painter, cx, cy, cw, ch)
        if self.pixmap:
            cw = cw!=-1 and cw or self.viewport().width()
            ch = ch!=-1 and ch or self.viewport().height()
            painter.drawPixmap(cx, cy, self.pixmap, cx, cy, min(cw, self.pixmap.width()-cx), min(ch, self.pixmap.height()-cy))
            painter.save()

            painter.setPen(QPen(Qt.blue, 2, Qt.SolidLine))
            painter.setBrush(QBrush(Qt.NoBrush))
            for rect in reduce(lambda a,b:a.union(b), [bbList for id, bbList in self.bbDict.items() if id in self.objects], set()):
                x1, y1, x2, y2 = map(lambda x:int(self.resizeFactor*x), rect)
                painter.drawRect(x1+1, y1+1, x2-x1, y2-y1)
                
            painter.setPen(QPen(Qt.red, 2, Qt.SolidLine))
            for rect in self.master.selectedObjects.keys():
                x1, y1, x2, y2 = map(lambda x:int(self.resizeFactor*x), rect)
                painter.drawRect(x1+1, y1+1, x2-x1, y2-y1)
            painter.restore()

    def GetObjects(self, x, y):
        def _in(x, y, bb):
##            if bb[0]=="rect":
            x1, y1, x2, y2 = map(lambda x:int(self.resizeFactor*x), bb)
            return x>=x1 and y>=y1 and x<x2 and y<y2
##            else:
##                x1, y1, r = map(lambda x:int(self.resizeFactor*x), bb[1:])
##                return abs(x1-x)<=r and abs(y1-y)<=r
        x, y = self.viewportToContents(x, y)
        objs = []
        for id, bbList in self.bbDict.items():
##            if id in self.objects:
            for bb in bbList:
                if _in(x, y, bb):
                    objs.append((id, bb))
        return objs
    
##    def viewportMouseMoveEvent(self, event):
##        x, y = event.x(), event.y()
##        objs = self.GetObjects(x, y)

    def viewportMousePressEvent(self, event):
        x, y = event.x(), event.y()
        old = set(self.master.selectedObjects.keys())
        objs = self.GetObjects(x, y)
        if event.button()==Qt.LeftButton:
            self.master.SelectObjects([(id, bb) for (id, bb) in objs if id in self.objects])
            for rect in set(self.master.selectedObjects.keys()).union(old):
                x1, y1, x2, y2 = map(lambda x:int(self.resizeFactor*x), rect)
                self.updateContents(x1-1, y1-1, x2-x1+2, y2-y1+2)
        elif event.button()==Qt.RightButton:
            self.popup.objs = objs
            self.popup.setItemEnabled(0, any(id for id, bb in objs if id in self.objects))
            self.popup.setItemEnabled(2, len(objs)==1 and objs[-1][0].startswith("path:"))
            self.popup.popup(self.mapToGlobal(event.pos()))
        else:
            QScrollView.viewportMousePressEvent(self, event)

    def resizeEvent(self, event):
        QScrollView.resizeEvent(self, event)
        if self.master.autoResize and self.image:
            self.ShowImage()

    def PopupAction(self, id):
        import webbrowser
        if id==0:
            genes = [s.split(":")[-1].strip() for s, t in self.popup.objs if s in self.objects]
            address = "http://www.genome.jp/dbget-bin/www_bget?"+self.pathway.org+"+"+"+".join(genes)
        elif id==1:
##            genes = [s for s, t in self.popup.objs]
##            s = reduce(lambda s,g:s.union(self.master.org.get_enzymes_by_gene(g)), genes, set())
##            address = "http://www.genome.jp/dbget-bin/www_bget?enzyme+"+"+".join([e.split(":")[-1] for e in s])
            genes = [s.split(":")[-1].strip() for s, t in self.popup.objs if s in self.objects]
            address = "http://www.genome.jp/dbget-bin/show_pathway?"+self.pathway.pathway_id.split(":")[-1]+(genes and "+"+"+".join(genes) or "")
        elif id==2:
            self.master.selectedObjects = defaultdict(list)
            self.master.Commit()
            self.SetPathway(obiKEGG.KEGGPathway(self.popup.objs[-1][0]))
            return
        try:
            webbrowser.open(address)
        except:
            pass
    
class OWKEGGPathwayBrowser(OWWidget):
    settingsList = ["organismIndex", "geneAttrIndex", "autoCommit", "autoResize", "useReference", "useAttrNames", "caseSensitive"]
    contextHandlers = {"":DomainContextHandler("",[ContextField("organismIndex", DomainContextHandler.Required + DomainContextHandler.IncludeMetaAttributes),
                                                   ContextField("geneAttrIndex", DomainContextHandler.Required + DomainContextHandler.IncludeMetaAttributes),
                                                   ContextField("useAttrNames", DomainContextHandler.Required + DomainContextHandler.IncludeMetaAttributes)])}
    def __init__(self, parent=None, signalManager=None, name="KEGG Pathway browser"):
        OWWidget.__init__(self, parent, signalManager, name)
        self.inputs = [("Examples", ExampleTable, self.SetData), ("Reference", ExampleTable, self.SetRefData)]
        self.outputs = [("Selected Examples", ExampleTable), ("Unselected Examples", ExampleTable)]
        self.organismIndex = 0
        self.geneAttrIndex = 0
        self.autoCommit = False
        self.autoResize = True
        self.useReference = False
        self.useAttrNames = False
        self.caseSensitive = True
        self.showOntology = True
        self.autoFindBestOrg = False
        self.loadSettings()

        self.controlArea.setMaximumWidth(250)
        self.organismCodes = obiKEGG.KEGGInterfaceLocal().list_organisms().items()
        self.organismCodes.sort()
        items = [code+": "+desc for code, desc in self.organismCodes]
        self.organismCodes = [code for code, desc in self.organismCodes]
        cb = OWGUI.comboBox(self.controlArea, self, "organismIndex", box="Organism", items=items, callback=self.Update, addSpace=True)
        cb.setMaximumWidth(200)
        
        box = OWGUI.widgetBox(self.controlArea, "Gene attribure")
        self.geneAttrCombo = OWGUI.comboBox(box, self, "geneAttrIndex", callback=self.Update)
        OWGUI.checkBox(box, self, "useAttrNames", "Use variable names", callback=self.UseAttrNamesCallback)
        OWGUI.checkBox(box, self, "caseSensitive", "Case sensitive gene matching", callback=self.Update)
        OWGUI.separator(self.controlArea)
        
        self.geneAttrCombo.setDisabled(bool(self.useAttrNames))
        
        OWGUI.checkBox(self.controlArea, self, "useReference", "From signal", box="Reference", callback=self.Update)
        OWGUI.separator(self.controlArea)

        OWGUI.checkBox(self.controlArea, self, "showOntology", "Show pathways in full ontology", box="Ontology", callback=self.UpdateListView)
        
        OWGUI.checkBox(self.controlArea, self, "autoResize", "Resize to fit", box="Image", callback=lambda :self.pathwayView.image and self.pathwayView.ShowImage())
        OWGUI.separator(self.controlArea)

        box = OWGUI.widgetBox(self.controlArea, "Selection")
        OWGUI.checkBox(box, self, "autoCommit", "Commit on update")
        OWGUI.button(box, self, "Commit", callback=self.Commit)
        OWGUI.rubber(self.controlArea)

        self.mainAreaLayout = QVBoxLayout(self.mainArea, QVBoxLayout.TopToBottom)
        spliter = QSplitter(Qt.Vertical, self.mainArea)
        self.pathwayView = PathwayView(self, spliter)
        self.mainAreaLayout.addWidget(spliter)

        self.listView = QListView(spliter)
        for header in ["Pathway", "P value", "Genes", "Reference"]:
            self.listView.addColumn(header)
        self.listView.setSelectionMode(QListView.Single)
        self.listView.setSorting(1)
        #self.listView.setAllColumnsShowFocus(1)
        self.listView.setMaximumHeight(200)
        
        self.connect(self.listView, SIGNAL("selectionChanged ( QListViewItem * )"), self.UpdatePathwayView)
        
        self.ctrlPressed=False
        self.selectedObjects = defaultdict(list)
        self.refData = None
        self.loadedOrganism = None
        
    def SetData(self, data=None):
        self.closeContext()
        self.data = data
        if data:
            self.SetBestGeneAttrAndOrganism()
            self.openContext("", data)
            self.Update()
        else:
            self.listView.clear()
            self.selectedObjects = defaultdict(list)
            self.pathwayView.SetPathway(None)
            self.send("Selected Examples", None)
            self.send("Unselected Examples", None)

    def SetRefData(self, data=None):
        self.refData = data
        if self.useReference and self.data:
            self.Update()

    def UseAttrNamesCallback(self):
        self.geneAttrCombo.setDisabled(bool(self.useAttrNames))
        self.Update()

    def SetBestGeneAttrAndOrganism(self):
        self.geneAttrCandidates = self.data.domain.attributes + self.data.domain.getmetas().values()
        self.geneAttrCandidates = filter(lambda v:v.varType in [orange.VarTypes.Discrete ,orange.VarTypes.String], self.geneAttrCandidates)
        self.geneAttrCombo.clear()
        self.geneAttrCombo.insertStrList([var.name for var in self.geneAttrCandidates])
        data = self.data
        if len(data)>20:
            data = data.select(orange.MakeRandomIndices2(data, 20))
        from cPickle import load
        score = {}
        self.progressBarInit()
        attrNames = [str(v.name).strip() for v in self.data.domain.attributes]
        testOrgs = self.autoFindBestOrg and self.organismCodes or [self.organismCodes[self.organismIndex]]
        for i, org in enumerate(testOrgs):
            try:
                geneNames = load(open(os.path.join(obiKEGG.default_database_path, org+"_genenames.pickle")))
            except:
                continue
            for attr in self.geneAttrCandidates:
                vals = [str(e[attr]).strip() for e in data if not e[attr].isSpecial()]
                vals = reduce(list.__add__, (split_and_strip(val, ",") for val in vals), [])
                match = filter(lambda v:v in geneNames, vals)
                score[(attr, org)] = len(match)
            match = [v for v in attrNames if v in geneNames]
            score[("_var_names_", org)] = len(match)
            self.progressBarSet(i*100.0/len(self.organismCodes))
        self.progressBarFinished()
        score = [(s, attr, org) for (attr, org), s in score.items()]
        score.sort()
        if not score:
            self.useAttrNames = False
            self.geneAttrIndex = len(self.geneAttrCandidates)-1
            self.organismIndex = 0
        elif score[-1][1]=="_var_names_":
            self.useAttrNames = True
            self.geneAttrIndex = 0 #self.geneAttrCandidates.index(score[-2][1])
            self.organismIndex = self.organismCodes.index(score[-1][2])
        else:
            self.useAttrNames = False
            self.geneAttrIndex = self.geneAttrCandidates.index(score[-1][1])
            self.organismIndex = self.organismCodes.index(score[-1][2])
        self.geneAttrCombo.setDisabled(bool(self.useAttrNames))
                
    def UpdateListView(self):
        self.listView.clear()
        allPathways = self.org.list_pathways()
        allRefPathways = obiKEGG.KEGGInterfaceLocal().list_pathways(org="map")
        items = []
        if self.showOntology:
            self.koOrthology = obiKEGG.KEGGInterfaceLocal().get_ko_orthology()
            self.listView.setRootIsDecorated(True)
            path_ids = set([s[-5:] for s in self.pathways.keys()])
            def _walkCollect(koClass):
                if koClass.ko_class_id in path_ids:
                    return [koClass]
                else:
                    c = reduce(lambda li,c:li+_walkCollect(c), [child for child in koClass.children], [])
                    return c + (c and [koClass] or [])
            allClasses = reduce(lambda li1, li2: li1+li2, [_walkCollect(c) for c in self.koOrthology], [])
            def _walkCreate(koClass, lvItem):
                item = QListViewItem(lvItem)
                id = "path:"+self.organismCodes[self.organismIndex]+koClass.ko_class_id
                if koClass.ko_class_id in path_ids:
                    genes, p_value, ref = self.pathways[id]
                    item.setText(0, allPathways.get(id, id))
                    item.setText(1, "%.5f" % p_value)
                    item.setText(2, "%i of %i" %(len(genes), len(self.genes)))
                    item.setText(3, "%i of %i" %(ref, len(self.referenceGenes)))
                    item.pathway_id = id
                else:
                    item.setText(0, allPathways.get(id, koClass.class_name))
                    if id in allPathways:
                        item.pathway_id = id
                    elif "path:map"+koClass.ko_class_id in allRefPathways:
                        item.pathway_id = "path:map"+koClass.ko_class_id
                    else:
                        item.pathway_id = None
                
                for child in koClass.children:
                    if child in allClasses:
                        _walkCreate(child, item)
                item.setOpen(True)                        
            
            for koClass in self.koOrthology:
                if koClass in allClasses:
                    _walkCreate(koClass, self.listView)
            self.listView.triggerUpdate()
        else:
            self.listView.setRootIsDecorated(False)
            pathways = self.pathways.items()
            pathways.sort(lambda a,b:cmp(a[1][1], b[1][1]))
            for id, (genes, p_value, ref) in pathways:
                item = QListViewItem(self.listView)
                item.setText(0, allPathways.get(id, id))
                item.setText(1, "%.5f" % p_value)
                item.setText(2, "%i of %i" %(len(genes), len(self.genes)))
                item.setText(3, "%i of %i" %(ref, len(self.referenceGenes)))
                item.pathway_id = id
                items.append(item)
        self.bestPValueItem = items and items[0] or None

    def UpdatePathwayView(self, item=None):
        self.selectedObjects = defaultdict(list)
        self.Commit()
        item = item or self.bestPValueItem
        if not item or not item.pathway_id:
            self.pathwayView.SetPathway(None)
            return
        self.pathway = obiKEGG.KEGGPathway(item.pathway_id)
        self.pathway.api.download_progress_callback = self.progressBarSet
        self.pathwayView.SetPathway(self.pathway, self.pathways.get(item.pathway_id, [[]])[0])
        
    def Update(self):
        if not self.data:
            return
        self.error(0)
        self.information(0)
        if self.useAttrNames:
            genes = [str(v.name).strip() for v in self.data.domain.attributes]
        elif self.geneAttrCandidates:
            geneAttr = self.geneAttrCandidates[min(self.geneAttrIndex, len(self.geneAttrCandidates)-1)]
            genes = [str(e[geneAttr]) for e in self.data if not e[geneAttr].isSpecial()]
            if any("," in gene for gene in genes):
                genes = reduce(list.__add__, (split_and_strip(gene, ",") for gene in genes), [])
                self.information(0, "Separators detected in input gene names. Assuming multiple genes per example.")
        else:
            self.error(0, "Cannot extact gene names from input")
            genes = []
        if self.loadedOrganism!=self.organismCodes[self.organismIndex]:
            self.org = obiKEGG.KEGGOrganism(self.organismCodes[self.organismIndex])
            self.org.api.download_progress_callback=self.progressBarSet
            self.loadedOrganism = self.organismCodes[self.organismIndex]
        self.progressBarInit()
        uniqueGenes, conflicting, unknown = self.org.get_unique_gene_ids(set(genes), self.caseSensitive)
        self.progressBarFinished()
        if conflicting:
            print "Conflicting genes:", conflicting
        if unknown:
            print "Unknown genes:", unknown
        self.information(1)
        if self.useReference and self.refData:
            if self.useAttrNames:
                reference = [str(v.name).strip() for v in self.refData]
            else:
                geneAttr = self.geneAttrCandidates[min(self.geneAttrIndex, len(self.geneAttrCandidates)-1)]
                reference = [str(e[geneAttr]) for e in self.refData if not e[geneAttr].isSpecial()]
                if any("," in gene for gene in reference):
                    reference = reduce(list.__add__, (split_and_strip(gene, ",") for gene in reference), [])
                    self.information(1, "Separators detected in reference gene names. Assuming multiple genes per example.")
            self.progressBarInit()
            uniqueRefGenes, conflicting, unknown = self.org.get_unique_gene_ids(set(reference), self.caseSensitive)
            self.progressBarFinished()
            self.referenceGenes = reference = uniqueRefGenes.keys()
        else:
            self.referenceGenes = reference = self.org.get_genes()
        self.uniqueGenesDict = uniqueGenes
        self.genes = uniqueGenes.keys()
        self.revUniqueGenesDict = dict([(val, key) for key, val in self.uniqueGenesDict.items()])
        self.progressBarInit()
        self.pathways = self.org.get_enriched_pathways_by_genes(self.genes, reference, callback=self.progressBarSet)
        self.progressBarFinished()
        self.UpdateListView()
        self.listView.setSelected(self.bestPValueItem, True)
        #self.UpdatePathwayView()

    def SelectObjects(self, objs):
        if (not self.selectedObjects or self.ctrlPressed) and not objs:
            return
        if self.ctrlPressed:
            for id, rect in objs:
                if id in self.selectedObjects[rect]:
                    self.selectedObjects[rect].pop(self.selectedObjects[rect].index(id))
                    if not self.selectedObjects[rect]:
                        del self.selectedObjects[rect]
                else:
                    self.selectedObjects[rect].append(id)
        else:
            self.selectedObjects.clear()
            for id, rect in objs:
                self.selectedObjects[rect].append(id)
        if self.autoCommit:
            self.Commit()
            

    def Commit(self):
        if self.useAttrNames:
            selectedGenes = reduce(set.union, self.selectedObjects.values(), set())
            selectedVars = [self.data.domain[self.uniqueGenesDict[gene]] for gene in selectedGenes]
            newDomain = orange.Domain(selectedVars ,0)
            self.send("Selected Examples", orange.ExampleTable(newDomain, self.data))
        else:
            geneAttr = self.geneAttrCandidates[min(self.geneAttrIndex, len(self.geneAttrCandidates)-1)]
            selectedExamples = []
            otherExamples = []
            selectedGenes = reduce(set.union, self.selectedObjects.values(), set())
            for ex in self.data:
                names = [self.revUniqueGenesDict.get(name, None) for name in split_and_strip(str(ex[geneAttr]), ",")]
                if any(name and name in selectedGenes for name in names):
                    selectedExamples.append(ex)
                else:
                    otherExamples.append(ex)
            self.send("Selected Examples", selectedExamples and orange.ExampleTable(selectedExamples) or None)
            self.send("Unselected Examples", otherExamples and orange.ExampleTable(otherExamples) or None)
        
    def keyPressEvent(self, key):
        if key.key()==Qt.Key_Control:
            self.ctrlPressed=True
        else:
            OWWidget.keyPressEvent(self, key)

    def keyReleaseEvent(self, key):
        if key.key()==Qt.Key_Control:
            self.ctrlPressed=False
        else:
            OWWidget.keyReleaseEvent(self, key)

if __name__=="__main__":
    app = QApplication(sys.argv)
    data = orange.ExampleTable("../../orange/doc/datasets/brown-selected.tab")
    w = OWKEGGPathwayBrowser()
    app.setMainWidget(w)
    w.show()
    w.SetData(data)
    app.exec_loop()
    w.saveSettings()
