from pathlib import Path
from html import escape
import csv
import subprocess

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image, Table, TableStyle, KeepTogether

BASE = Path(__file__).resolve().parents[1]
REPO = BASE.parent
OUT = BASE / '实验报告.pdf'
pdfmetrics.registerFont(TTFont('SimHei', r'C:\Windows\Fonts\simhei.ttf'))

NAVY = colors.HexColor('#18324A')
BLUE = colors.HexColor('#245F95')
MUTED = colors.HexColor('#617487')
RULE = colors.HexColor('#D8E5ED')
styles = {
    'title': ParagraphStyle('t',fontName='SimHei',fontSize=23,leading=30,textColor=NAVY,spaceAfter=15),
    'h1': ParagraphStyle('h1',fontName='SimHei',fontSize=14,leading=20,textColor=BLUE,spaceBefore=15,spaceAfter=8),
    'h2': ParagraphStyle('h2',fontName='SimHei',fontSize=11,leading=17,textColor=BLUE,spaceBefore=10,spaceAfter=6),
    'body': ParagraphStyle('body',fontName='SimHei',fontSize=9.2,leading=16,textColor=NAVY,wordWrap='CJK',spaceAfter=7),
    'small': ParagraphStyle('small',fontName='SimHei',fontSize=7.5,leading=12,textColor=MUTED,wordWrap='CJK',spaceAfter=5),
    'cell': ParagraphStyle('cell',fontName='SimHei',fontSize=7.2,leading=10,textColor=NAVY,wordWrap='CJK'),
}

def P(text, style='body'):
    return Paragraph(escape(text), styles[style])

def H(text): return P(text,'h1')
def SH(text): return P(text,'h2')

def figure(path, maxw=500, maxh=660):
    from PIL import Image as PILImage
    im=PILImage.open(path)
    w,h=im.size
    scale=min(maxw/w,maxh/h)
    return Image(str(path),width=w*scale,height=h*scale)

def page(canvas, doc):
    canvas.saveState()
    width,height=A4
    canvas.setStrokeColor(RULE)
    canvas.line(45,height-43,width-45,height-43)
    canvas.setFont('SimHei',8)
    canvas.setFillColor(MUTED)
    canvas.drawString(45,height-32,'软件工程实验二  /  二手交易系统设计与建模')
    canvas.drawRightString(width-45,27,str(doc.page))
    canvas.restoreState()

def git(*args): return subprocess.check_output(['git',*args],cwd=REPO,text=True).strip()

story=[Spacer(1,22),P('二手交易系统：软件设计与建模','title'),P('王旭东  ·  学号 241220020  ·  需求访谈甲方 张鸣远（241220137）'),P('设计基线：实验一 lab1-final  ·  2026-10-31'),Spacer(1,12)]
story += [H('1  项目与系统范围'),P('本项目面向同一账号兼具买卖身份的二手商品信息撮合。系统包含注册登录、商品发布审核、搜索、预约与线下交易确认、举报、站内通知、统计和操作日志。真实支付和物流不在范围内。'),P('设计基线为实验一最终需求。REQ-037 普通下架商品能否重新上架、REQ-038 其他字段修改是否重审仍待确认；管理员账号开通主体和审核流程也未由访谈确定。设计图不为这些规则补造确定性路径。'),H('2  主要功能与模块划分')]
modules=[
('认证与权限','手机号和用户名唯一；买卖家同账号；后台只允许独立管理员账号。'),
('商品与审核','草稿、提交、通过、驳回、改价/描述重审、主动下架、已售出保留。'),
('预约与交易','创建待确认预约，同意/拒绝，取消、24小时超时、线下交易完成与联系方式权限。'),
('举报与通知','举报原因、管理员核实、违规强制下架，向举报人展示站内处理结果。'),
('查询与统计','买家组合搜索、卖家状态筛选、管理员每日上架数与分类在售数。'),
('日志与数据','所有商品和预约状态变化、审核及举报可追溯；历史业务数据保留。'),
]
for name,desc in modules: story.append(P('• '+name+'：'+desc))
story += [P('组件调用从界面进入权限入口，再由各业务模块完成校验和状态迁移。预约服务与商品服务联动；举报违规时调用预约服务终止在途预约；超时任务仅处理仍待确认的预约。业务数据通过同一数据访问边界持久化。'),PageBreak()]

story += [H('3  数据关系、状态和一致性'),P('核心数据实体为普通用户、管理员、商品、预约、举报、站内通知和操作日志。商品关联卖家；预约关联商品与买家；举报关联商品与举报人；通知关联接收人。手机号与用户名不得重复，同一商品同一时刻最多一条待确认或待交易的有效预约。'),P('商品主路径：草稿 → 待审核 → 已发布 → 已预约 → 待交易 → 已售出。驳回后可修改重提；已发布且无有效预约时可主动下架；改价或描述退回待审核。预约过程禁止卖家修改或主动下架。已预约可因拒绝、买家取消、24小时超时回到已发布；待交易可由买卖任一方取消。违规下架为不可恢复的终止状态。'),P('预约状态：待确认、待交易为有效；已拒绝、已取消、超时取消、已完成为终结。商品已预约必须对应待确认预约，待交易必须对应待交易预约，已发布不得存在有效预约。状态和联系方式权限需同步变化。'),P('建议关系型数据库建立用户手机号/用户名唯一约束、外键和商品状态字段；预约创建、取消、完成、超时及强制下架在同一业务事务中更新商品、预约与日志。并发下应原子争取唯一有效预约；重复提交先识别当前买家已有预约；旧页面操作以服务端最新状态为准。具体数据库产品、字段类型与索引方案属于设计选择，未写成甲方确认。'),H('4  UML 选型与说明'),P('类图、组件图分别表达实体结构和模块结构；状态图用于检查允许与禁止的商品状态变换；时序图检查预约时的并发、重复点击和旧页面处理。这样满足结构、行为、交互三类中至少各一种，且包含必画的类图。'),PageBreak()]

uml=[
('UML类图','商品、预约、用户、管理员、举报、通知、日志的关联；预约有效状态约束位于图中。'),
('UML组件图','界面、权限入口、六个业务模块、超时任务及数据库的逻辑交互。'),
('UML状态图','商品主路径与驳回、取消、下架、违规分支；普通下架后重新上架保持待确认。'),
('UML时序图','同一买家重复点击、他人抢先、旧页面状态变化与成功预约的互斥分支。'),
]
for i,(name,desc) in enumerate(uml,1):
    story += [H(f'4.{i}  {name}'),P(desc),figure(BASE/'UML'/f'{name}.png'),P(f'可编辑源文件：UML/{name}.puml；完整分辨率 PNG 同目录。','small'),PageBreak()]

story += [H('5  各模块 UI 设计'),P('在线 Figma/Pixso 未能通过当前浏览器自动化可靠操作。可编辑 SVG 由脚本生成，实际安装的 Inkscape 1.4.4 负责读取 SVG 并导出 PNG；这些界面不是 Figma/Pixso 成果。界面采用统一的深蓝、浅灰和绿色状态标识。所有金额、统计值、商品和日志行仅为演示样例，不代表真实业务数据。角色可见操作由服务端权限再次约束。')]
ui_desc={
'01_账号登录':'普通用户手机号登录与注册入口；管理员使用独立入口。重复手机号或用户名应给出明确提示。',
'02_商品搜索':'关键词、分类、价格区间组合筛选，仅展示当前已发布商品。',
'03_搜索空状态':'无结果时给出清除筛选按钮，避免空白页面。',
'04_商品详情预约':'提交预约前复核当前商品状态；卖家同意前隐藏联系方式。',
'05_预约结果与冲突':'区分预约成功、他人抢先、本人重复提交和旧页面状态变化。',
'06_发布草稿':'卖家编辑标题、分类、价格和描述，保存草稿或提交审核。',
'07_卖家商品管理':'卖家按状态筛选自有商品；预约中的编辑和主动下架不可用；售出保留。',
'08_预约交易':'买家查看待确认/待交易、取消预约或交易；买家没有确认完成按钮。',
'08b_卖家处理预约':'卖家同意或拒绝待确认预约，待交易后可取消或确认线下完成。',
'09_举报与通知':'买家填写举报原因，并在站内查看管理员处理结果。',
'10_管理员审核':'管理员审核商品和举报；违规下架联动终止有效预约。',
'11_管理统计日志':'管理员查看两项统计口径及按商品、操作人、时间筛选日志。',
}
for i,(stem,desc) in enumerate(ui_desc.items(),1):
    story += ([PageBreak()] if i > 1 else []) + [H(f'5.{i}  {stem}'),P(desc),figure(BASE/'UI界面'/f'{stem}.png',maxw=500,maxh=340),P('设计源文件：设计源文件/'+stem+'.svg；导出图：UI界面/'+stem+'.png。','small')]

story += [PageBreak(),H('6  需求—设计追踪'),P('下表逐条把实验一 REQ 编号关联到设计模块、UML 和 UI。完整可机读版本在仓库的“实验二/需求设计追踪矩阵.csv”。待确认的 REQ-037、REQ-038 只标设计边界，不视为已确认业务行为。')]
with (BASE/'需求设计追踪矩阵.csv').open(encoding='utf-8-sig',newline='') as f:
    rows=list(csv.DictReader(f))
assert len(rows)==38
data=[[P('需求','cell'),P('模块','cell'),P('UML','cell'),P('UI编号','cell')]]
for row in rows:
    imgs=', '.join(x.split('_')[0] for x in row['UI界面'].split(','))
    data.append([P(row['实验一需求ID'],'cell'),P(row['设计模块'],'cell'),P(row['UML图'],'cell'),P(imgs,'cell')])
table=Table(data,colWidths=[75,120,125,180],repeatRows=1,hAlign='LEFT')
table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E8F1F7')),('GRID',(0,0),(-1,-1),0.35,RULE),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
story += [table,PageBreak()]

story += [H('7  AI 辅助设计与反思'),P('本实验使用本次 Codex 会话辅助整理架构、生成 PlantUML 源文件、可编辑 SVG 界面和报告草稿。具体输入、产物、问题与修订见仓库“实验二/AI辅助记录.md”；模型精确标识未由当前可核验界面提供，故不杜撰型号。'),SH('7.1  生成、检查与修订'),P('UML 初稿形成四种视图，但实际 PlantUML 编译暴露了类图中文名称语法错误；图片检查显示组件图过密。修复别名写法、调整布局和连线后重新编译。UI 初稿把买家和卖家放在同一预约界面，并误给买家显示卖家确认按钮；按角色拆分后再导出。'),P('优点是生成速度快、跨图复用术语方便；局限是语法正确性、角色权限与状态一致性不能仅凭流畅文字保证。此次实际使用了编译、逐图查看、需求追踪及文件清单核对。AI 生成的设计不能当作甲方确认，待确认需求仍保持开放。'),SH('7.2  个人反思草稿（待本人确认）'),P('AI 生成—自动规则检查—人工确认可以减少重复绘制和整理工作，但人不能只作形式检查。图和界面可能在关键按钮权限、并发结果或状态同步上出错，需要基于访谈判断并负责最终取舍。此段是供王旭东本人审阅、修改的反思草稿，不代表本人已完成这些实际操作。'),H('8  Git 与交付信息'),P('实验一标签 lab1-ai-draft、lab1-final 保持原位；实验二在同一仓库的独立目录中，先提交 UML AI 初稿，再提交 UI AI 初稿，随后提交语法修复、权限优化、报告和追踪矩阵。'),P('仓库：'+git('remote','get-url','origin')),P('UML 初稿提交：78fb2c2；UI 初稿提交：c7bd8ce。最终提交以仓库 main 为准。'),P('最终压缩包只包含实验报告 PDF、四对 UML PNG/PUML、全部 UI PNG。SVG 源文件、脚本、追踪矩阵和 AI 过程记录保存在仓库中。')]

doc=SimpleDocTemplate(str(OUT),pagesize=A4,leftMargin=45,rightMargin=45,topMargin=55,bottomMargin=43,title='实验二：二手交易系统软件设计与建模',author='王旭东')
doc.build(story,onFirstPage=page,onLaterPages=page)
print(OUT)
