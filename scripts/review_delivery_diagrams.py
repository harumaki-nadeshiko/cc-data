#!/usr/bin/env python3
"""Publication-size technical diagrams: one point-based scene for SVG/PNG/draw.io."""
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle, Circle, FancyArrowPatch, Polygon

OUT = Path('/work/docs/design/figures')
for p in Path('/usr/local/share/fonts').rglob('*'):
    if p.suffix.lower() in ('.ttf', '.otf', '.ttc'):
        font_manager.fontManager.addfont(str(p))
plt.rcParams.update({'font.family': 'Microsoft YaHei', 'svg.fonttype': 'none', 'font.size': 9})
BLUE, TEAL, GREY = '#284e76', '#387f83', '#606970'


class Scene:
    def __init__(self, stem, height):
        self.stem, self.height = stem, height
        self.fig = plt.figure(figsize=(6.1, height/72))
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set(xlim=(0, 439.2), ylim=(height, 0))
        self.ax.axis('off')
        self.xml = Element('mxfile', host='app.diagrams.net')
        diagram = SubElement(self.xml, 'diagram', name=stem)
        model = SubElement(diagram, 'mxGraphModel', pageWidth='439', pageHeight=str(height))
        self.root = SubElement(model, 'root')
        SubElement(self.root, 'mxCell', id='0')
        SubElement(self.root, 'mxCell', id='1', parent='0')
        self.count = 1

    def text(self, x, y, text, size=9, color=BLUE, ha='center'):
        self.ax.text(x, y, text, ha=ha, va='center', fontsize=size, color=color, linespacing=1.5)
        self.cell(text, x-80, y-15, 160, 30, 'text;strokeColor=none;fillColor=none;', size)

    def cell(self, label, x, y, w, h, style, size=9):
        self.count += 1
        c = SubElement(self.root, 'mxCell', id=str(self.count), parent='1', vertex='1', value=label,
                       style=style+f'fontFamily=Microsoft YaHei;fontSize={size};whiteSpace=wrap;html=0;')
        SubElement(c, 'mxGeometry', x=str(x), y=str(y), width=str(w), height=str(h), attrib={'as': 'geometry'})

    def box(self, x, y, w, h, label, fill='#f0f5f8', color=BLUE):
        self.ax.add_patch(Rectangle((x,y),w,h,facecolor=fill,edgecolor=color,linewidth=.8))
        self.ax.text(x+w/2,y+h/2,label,ha='center',va='center',fontsize=9,color=color,linespacing=1.5)
        self.cell(label,x,y,w,h,f'rounded=0;fillColor={fill};strokeColor={color};')

    def arrow(self, x1,y1,x2,y2, label='', dashed=False):
        self.ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),arrowstyle='-|>',mutation_scale=9,
                                        linewidth=.8,color=GREY,linestyle='--' if dashed else '-'))
        if label:
            self.ax.text((x1+x2)/2,(y1+y2)/2-7,label,ha='center',va='bottom',fontsize=9,color=GREY,
                         bbox=dict(facecolor='white',edgecolor='none',pad=1))
        self.count += 1
        c = SubElement(self.root,'mxCell',id=str(self.count),parent='1',edge='1',value=label,
                       style='endArrow=block;fontFamily=Microsoft YaHei;fontSize=9;'+('dashed=1;' if dashed else ''))
        g=SubElement(c,'mxGeometry',relative='1',attrib={'as':'geometry'})
        SubElement(g,'mxPoint',x=str(x1),y=str(y1),attrib={'as':'sourcePoint'})
        SubElement(g,'mxPoint',x=str(x2),y=str(y2),attrib={'as':'targetPoint'})

    def save(self):
        for suffix in ('png','svg'):
            self.fig.savefig(OUT/f'{self.stem}.{suffix}',dpi=300)
        (OUT/f'{self.stem}.drawio').write_bytes(tostring(self.xml,encoding='utf-8',xml_declaration=True))
        plt.close(self.fig)


def architecture():
    s=Scene('ubcc-system-architecture',320)
    for x, title in [(12,'节点 A'),(235,'节点 B … N')]:
        s.text(x+96,14,title,11)
        s.box(x,32,192,45,'CPU 与私有缓存 → HN-F／L3')
        s.box(x,104,192,43,'EP-RNF／EP-SNF\nEPBackend + UBAdapter','#f8f4e9')
        s.arrow(x+96,77,x+96,104)
        s.box(x,180,192,42,'Home UBCC 控制器','#edf4ef',TEAL)
        s.arrow(x+96,147,x+96,180)
        s.box(x,248,90,38,'ResidentDir')
        s.box(x+102,248,90,38,'H64 元数据')
        s.arrow(x+48,222,x+48,248)
        s.arrow(x+144,222,x+144,248)
    s.arrow(204,125,235,125)
    s.text(219,307,'跨节点消息按 Home 与事务身份路由；数据不存入 H64',9)
    s.save()


def endpoints():
    s=Scene('gem5-ruby-controller-relationships',342)
    s.text(220,15,'节点内原生 CHI 与 EP 边界',11)
    for x,label in [(12,'CPU / L1'),(155,'私有 L2'),(298,'HN-F / L3')]:
        s.box(x,40,128,35,label)
    s.arrow(140,57,155,57); s.arrow(283,57,298,57)
    s.box(12,117,128,42,'L-SNF\n本地内存')
    s.box(155,117,128,42,'EP-RNF\nRecall／失效')
    s.box(298,117,128,42,'EP-SNF\n请求／写回')
    s.arrow(360,90,360,117); s.arrow(219,90,219,117); s.arrow(76,90,76,117)
    s.ax.plot([76,360],[90,90],color=GREY,linewidth=.8)
    s.arrow(360,75,360,90)
    s.box(155,195,271,57,'节点级 EPBackend\nRecall／写回协调：64 项，4.5 KiB','#f8f4e9')
    s.arrow(219,159,219,195); s.arrow(360,159,360,195)
    s.box(155,285,128,36,'UBAdapter')
    s.box(298,285,128,36,'Outer / Home')
    s.arrow(219,252,219,285); s.arrow(283,303,298,303)
    s.text(76,223,'缓存数据仍由\n原生 CHI 承载',9)
    s.save()


def sequences():
    s=Scene('ubcc-protocol-paths',444)
    for band,title,messages in [(0,'共享读',[(0,1,'请求'),(1,2,'Recall 数据源'),(2,1,'数据返回'),(1,0,'数据 + Grant')]),
                                (148,'所有权迁移',[(0,1,'请求独占'),(1,2,'回收旧 owner'),(2,1,'最新数据 + 释放'),(1,0,'新 owner 授权')]),
                                (296,'共享转写者',[(0,1,'请求写权限'),(1,2,'精确目标失效'),(2,1,'Ack 收敛'),(1,0,'Grant；随后 Clear')])]:
        s.text(12,band+13,title,10,ha='left')
        xs=[68,220,372]
        for x,label in zip(xs,['requester','Home','owner／sharers']):
            s.text(x,band+35,label)
            s.ax.plot([x,x],[band+48,band+141],color='#bbc5cd',linewidth=.6)
        for j,(a,b,label) in enumerate(messages):
            s.arrow(xs[a],band+60+j*24,xs[b],band+60+j*24,label)
    s.save()


def state():
    s=Scene('ubcc-two-phase-commit',248)
    coords=[(72,83,'Committed'),(219,83,'Reserved'),(365,83,'Granted')]
    for x,y,label in coords:
        s.ax.add_patch(Circle((x,y),37,facecolor='#f0f5f8',edgecolor=BLUE,linewidth=.8))
        s.text(x,y,label)
        s.cell('',x-37,y-37,74,74,'ellipse;fillColor=none;strokeColor=#284e76;')
    s.arrow(109,83,182,83,'请求'); s.arrow(256,83,328,83,'Grant')
    s.arrow(365,120,365,170); s.arrow(365,170,72,170,'匹配 Clear：提交 intended state'); s.arrow(72,170,72,120)
    s.text(220,215,'Grant 在途：保留原 committed state\n重复请求沿原身份重发；过期 Clear 不提交',9)
    s.save()


def verification():
    s=Scene('ubcc-verification-stack',224)
    for i,(label,half) in enumerate([('故障恢复：丢失／重复／乱序',112),('端到端：数据、权限与完成',143),('定向交互：并发、等待与退役',174),('形式化：Safety 与条件化 Liveness',205)]):
        y=12+i*50
        s.ax.add_patch(Polygon([(219-half+15,y),(219+half-15,y),(219+half,y+40),(219-half,y+40)],facecolor='#f0f5f8',edgecolor=BLUE,linewidth=.8))
        s.text(219,y+20,label)
        s.cell('',219-half,y,half*2,40,'shape=trapezoid;fillColor=none;strokeColor=#284e76;')
    s.save()


def comparisons():
    s=Scene('ubcc-protocol-authority-comparison',255)
    s.text(114,18,'状态集合',11); s.text(326,18,'Outer 组织',11)
    for x,labels in [(14,['VI／MSI／MESI／MOESI／MESIF','表达副本权限与数据责任','当前全局目录：MESI 类']), (226,['Home-directory／CHI／探测','决定消息承载与数据路径','当前：专用目录，Home 中转'])]:
        for i,label in enumerate(labels):
            s.box(x,47+i*64,198,40,label)
            if i<2:s.arrow(x+99,87+i*64,x+99,111+i*64)
    s.text(219,238,'全局权限与提交权属于 Home；数据转发不转移提交权',9)
    s.save()
    s=Scene('ubcc-path-central-vs-direct',236)
    for y,title in [(20,'经 Home 返回'),(126,'直接数据转发（设计路径）')]:
        s.text(12,y,title,10,ha='left')
        for x,label in [(12,'requester'),(164,'Home'),(316,'数据源')]:s.box(x,y+29,110,34,label)
        s.arrow(122,y+46,164,y+46);s.arrow(274,y+46,316,y+46)
        if y==20:
            s.arrow(371,y+81,219,y+81,'数据');s.arrow(219,y+81,67,y+81)
        else:s.arrow(371,y+81,67,y+81,'数据直达；提交权仍在 Home')
    s.save()
    s=Scene('ubcc-metadata-fanout-scaling',358)
    s.text(219,16,'分层目录：查询、迁移与目标收敛',11)
    s.box(12,44,180,47,'ResidentDir：热点元数据\ntag／状态／epoch／sharer')
    s.box(246,44,180,47,'Bloom + GroupIndex\n过滤与后备分组定位')
    s.arrow(192,68,246,68,'miss')
    s.box(246,133,180,57,'H64：冷元数据\n64 B bucket\n4 B header + 5 × 12 B slot')
    s.arrow(336,91,336,133,'候选查询')
    s.arrow(246,166,192,166,'换入')
    s.arrow(192,166,102,91)
    s.arrow(102,91,102,212)
    s.box(12,212,414,43,'精确 committed 关系 → 本次目标集合 → Ack 去重收敛')
    s.text(219,281,'冷元数据换出至 H64；缓存行数据不写入 H64\nBloom 不直接授权，精确目录决定权限与失效目标',9)
    s.text(219,334,'位图按节点扩展；Socket 参与身份与节点内路由',9)
    s.save()
    s=Scene('ubcc-inner-chi-outer-boundary',279)
    s.box(12,20,130,48,'Inner CHI\n缓存与 HN-F')
    s.box(155,20,130,48,'EP 边界\n身份与完成关联','#f8f4e9')
    s.box(298,20,130,48,'Outer Home\n目录与提交')
    s.arrow(142,44,155,44);s.arrow(285,44,298,44)
    s.box(12,108,196,60,'数据责任\n原生 TBE／PendingWrite\n不建第二份缓存数据镜像')
    s.box(230,108,198,60,'交接描述符\n64 项 × 72 B／节点\n8 项控制预留')
    s.arrow(220,68,220,92);s.arrow(220,92,329,108)
    s.text(219,205,'首次 owner／epoch 快照 → 原生回收或写回\n发布确认 → 各路径完成 → generation 校验退役',9)
    s.text(219,258,'4.5 KiB 仅为协调表，非完整 EP 资源预算',9)
    s.save()


def compact_charts():
    import json
    data=json.loads(Path('/work/docs/design/performance_publication_data.json').read_text())
    def save(fig,stem):
        for ext in ('png','svg'):fig.savefig(OUT/f'{stem}.{ext}',dpi=300)
        plt.close(fig)
    # Small datasets read better as tables in the document.  Retain scalable
    # chart artifacts at their intended final physical size for editing.
    fig,axs=plt.subplots(1,2,figsize=(6.1,2.8))
    m=data['metric1']
    axs[0].bar(['TC131'],[m['capacity_ratio']],color=TEAL,width=.4)
    axs[0].axhline(1.5,color=GREY,ls='--',lw=.8);axs[0].set_ylabel('等效容量比（倍）')
    axs[1].bar(['TC131'],[m['outer_delta_mean_ns']],color=BLUE,width=.4)
    axs[1].axhline(25,color=GREY,ls='--',lw=.8);axs[1].set_ylabel('附加时延（ns）')
    for ax in axs:
        ax.spines['top'].set_visible(False);ax.spines['right'].set_visible(False)
        ax.tick_params(labelsize=9)
    fig.tight_layout();save(fig,'ubcc-metric1-capacity-latency')
    from publication_extension_charts import coordinates, TOPOLOGIES, APPLICATION_TOPOLOGIES
    extension=json.loads(Path('/work/docs/design/performance_extension_data.json').read_text())
    rows=coordinates(extension)
    colors=['#284e76','#387f83','#76914d','#ae8c4e','#8f738e']
    for stem,fields in [('ubcc-metric1-extension-matrix',('capacity_ratio','outer_delta_cycles_2ghz')),
                        ('ubcc-tc142-147-applications',('reduction_pct',))]:
        fig,axes=plt.subplots(2*len(fields),1,figsize=(6.1,6.8 if len(fields)==2 else 4.2))
        for fi,field in enumerate(fields):
            topologies=APPLICATION_TOPOLOGIES if field=='reduction_pct' else TOPOLOGIES
            for pi,pressure in enumerate((175,200)):
                ax=axes[fi*2+pi]
                for ti,topo in enumerate(topologies):
                    values=[next(r[field] for r in rows if r['tc']==tc and r['pressure_pct']==pressure and r['topology']==topo) for tc in range(142,148)]
                    if field=='outer_delta_cycles_2ghz':values=[v/2 for v in values]
                    ax.bar([i+(ti-(len(topologies)-1)/2)*.14 for i in range(6)],values,.13,label=topo.upper(),color=colors[ti])
                ax.set_xticks(range(6));ax.set_xticklabels([f'TC{tc}' for tc in range(142,148)],fontsize=9)
                ax.set_ylabel({'capacity_ratio':'容量比（倍）','outer_delta_cycles_2ghz':'附加时延（ns）','reduction_pct':'E2E 降幅（%）'}[field],fontsize=9)
                for ref in {'capacity_ratio':[1.5],'outer_delta_cycles_2ghz':[0,25],'reduction_pct':[0,10]}[field]:ax.axhline(ref,color=GREY,lw=.7,ls='--')
                ax.text(.99,.9,f'P{pressure}',ha='right',transform=ax.transAxes,fontsize=9,
                        bbox=dict(facecolor='white',edgecolor='none',pad=1))
                ax.tick_params(labelsize=9);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
                ax.spines['top'].set_visible(False);ax.spines['right'].set_visible(False)
        h,l=axes[0].get_legend_handles_labels();fig.legend(h,l,loc='upper center',ncol=5,frameon=False,fontsize=9)
        fig.subplots_adjust(left=.15,right=.98,top=.91,bottom=.08,hspace=.4)
        save(fig,stem)
    cases=[r for r in extension['metric2_original']['comparisons'] if r['applicable']]
    fig,ax=plt.subplots(figsize=(6.1,3.2))
    ax.bar([r['case'] for r in cases],[r['means_ns']['naive']/r['means_ns']['optimized'] for r in cases],color=BLUE,width=.5)
    ax.set_yscale('log');ax.axhline(1/.9,color=TEAL,ls='--',lw=.8,label='10% reduction')
    from matplotlib.ticker import FixedLocator, FixedFormatter, NullFormatter
    ax.yaxis.set_major_locator(FixedLocator([1,2,5,10,20,50,100]))
    ax.yaxis.set_major_formatter(FixedFormatter(['1','2','5','10','20','50','100']))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.tick_params(labelsize=9)
    ax.set_ylabel('naive / optimized');ax.legend(frameon=False,fontsize=9)
    ax.spines['top'].set_visible(False);ax.spines['right'].set_visible(False)
    fig.tight_layout();save(fig,'ubcc-metric2-reductions')


architecture(); endpoints(); sequences(); state(); verification(); comparisons(); compact_charts()

# Metadata follows the actual embedded figure set; retired small plots are
# retained as editable references, not counted as publication placements.
import json
import hashlib
import re
placements=[]
for md in OUT.parent.glob('cc_ep*.md'):
    for caption,stem in re.findall(r'!\[([^]]+)\]\(figures/([\w-]+)\.png[^)]*\)',md.read_text()):
        placements.append({'document':md.name,'caption':caption,'name':stem,
                           'source': 'performance_boundary_v4_m3.json' if stem in ('ubcc-ha-vi-comparison','ubcc-metric3-per-tc-reductions') else 'review_delivery_diagrams.py',
                           'physical_width_cm':15.5,'minimum_designed_font_pt':9,
                           'png_sha256':hashlib.sha256((OUT/(stem+'.png')).read_bytes()).hexdigest()})
(OUT/'boundary_v4_figure_inventory.json').write_text(json.dumps({'placements':placements,'review_scope':'Designed sizes; inspect Writer PDF for final placement.'},ensure_ascii=False,indent=2)+'\n')
