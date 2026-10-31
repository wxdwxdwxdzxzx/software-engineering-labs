"""Generate editable SVG UI boards; export the SVGs with Inkscape."""
from pathlib import Path
from html import escape

ROOT = Path(__file__).resolve().parent
ROOT.mkdir(parents=True, exist_ok=True)
W, H = 1440, 900
INK, MUTED, BLUE, PALE = '#18324A', '#617487', '#245F95', '#EEF5FA'


def rect(x, y, w, h, fill='#FFFFFF', radius=0, stroke='none', sw=1):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'


def txt(x, y, s, size=22, color=INK, weight=400, anchor='start'):
    return f'<text x="{x}" y="{y}" fill="{color}" font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{escape(s)}</text>'


def button(x, y, w, label, fill=BLUE, color='#FFFFFF'):
    return rect(x, y, w, 50, fill, 12) + txt(x+w/2, y+32, label, 18, color, 600, 'middle')


def pill(x, y, label, fill='#E3EFF8', color=BLUE, w=106):
    return rect(x, y, w, 32, fill, 16) + txt(x+w/2, y+22, label, 15, color, 500, 'middle')


def field(x, y, label, value, w=430):
    return txt(x, y, label, 16, MUTED, 500) + rect(x, y+13, w, 52, '#FFFFFF', 10, '#CBD8E2') + txt(x+16, y+47, value, 17)


def frame(title, role, active='发现', note='设计稿 · 2026-10-31'):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">', rect(0,0,W,H,'#F6F9FC'), rect(0,0,W,76,'#FFFFFF'), rect(0,75,W,1,'#D9E4EC')]
    s += [rect(34,22,34,34,BLUE,10),txt(51,46,'闲',20,'#FFFFFF',700,'middle'),txt(80,49,'拾光 · 二手交易',23,INK,700)]
    x=420
    links = ['管理后台'] if role == '管理员' else ['发现','发布商品','我的商品','我的预约']
    for nav in links:
        s += [txt(x,48,nav,17,BLUE if nav==active else MUTED,600 if nav==active else 400)]
        x += 145 if nav!='管理后台' else 0
    s += [pill(1220,23,role,'#E9F3EE','#277450',150),txt(45,135,title,30,INK,700),txt(45,167,note,14,MUTED)]
    return s


def end(parts, path):
    parts.append('</svg>')
    (ROOT/path).write_text('\n'.join(parts),encoding='utf-8')


def product(x,y,title,price,status='已发布',accent='#81B6CF'):
    return ''.join([rect(x,y,292,320,'#FFFFFF',18,'#DCE7EF'),rect(x+16,y+16,260,180,'#E6F2F5',12),rect(x+58,y+52,176,108,accent,22),txt(x+146,y+120,'闲置好物',25,'#FFFFFF',700,'middle'),txt(x+18,y+228,title,20,INK,600),txt(x+18,y+266,f'¥ {price}',25,'#D55D4E',700),pill(x+174,y+270,status)])


# 01: identity and permissions
s=frame('注册与登录','访客',active='发现',note='普通用户同一账号可买卖；管理员使用独立后台账号')
s += [rect(100,210,600,575,'#E8F1F8',24),txt(155,310,'让闲置找到下一位主人',34,INK,700),txt(155,362,'先注册或登录，再发布和预约商品。',19,MUTED),pill(155,420,'买家与卖家共用账号',w=220),rect(745,210,590,575,'#FFFFFF',24,'#DCE7EF'),txt(800,280,'手机号登录',27,INK,700),field(800,310,'手机号','请输入手机号'),field(800,400,'密码','••••••••'),button(800,505,430,'登录'),txt(800,595,'注册新账号 · 手机号和用户名不可重复',17,MUTED),pill(800,650,'管理员入口',w=135)]
end(s,'01_账号登录.svg')

# 02: search, filters, empty result
s=frame('发现闲置','买家',active='发现',note='关键词、分类、价格区间组合搜索；仅显示已发布商品')
s += [field(48,210,'搜索商品','搜索标题或描述',575),button(640,223,110,'搜索'),rect(48,320,280,430,'#FFFFFF',18,'#DCE7EF'),txt(72,370,'筛选条件',22,INK,700),txt(72,425,'分类  ·  数码 / 书籍 / 生活',17),txt(72,480,'价格  ·  ¥0 — ¥500',17),pill(72,525,'重置筛选',w=125),product(365,320,'机械键盘 · 九成新','180'),product(678,320,'设计书籍套装','75'),product(991,320,'桌面台灯','55')]
end(s,'02_商品搜索.svg')

s=frame('暂无符合条件的商品','买家',active='发现',note='空状态：调整关键词、分类或价格区间后重试')
s += [rect(65,225,1310,535,'#FFFFFF',22,'#DCE7EF'),txt(720,395,'没有找到符合条件的商品',28,INK,700,'middle'),txt(720,452,'试试放宽价格范围，或清除部分筛选条件。',19,MUTED,400,'middle'),button(610,510,220,'清除筛选条件')]
end(s,'03_搜索空状态.svg')

# 04: detail and reservation
s=frame('商品详情','买家',active='发现',note='提交预约时再次检查商品最新状态；未获同意前隐藏联系方式')
s += [rect(48,215,710,535,'#E6F2F5',22),rect(210,320,385,230,'#81B6CF',28),txt(402,445,'机械键盘',40,'#FFFFFF',700,'middle'),rect(800,215,585,535,'#FFFFFF',22,'#DCE7EF'),pill(835,250,'已发布'),txt(835,336,'机械键盘 · 九成新',29,INK,700),txt(835,395,'¥ 180',34,'#D55D4E',700),txt(835,465,'卖家：同校用户  |  分类：数码',17,MUTED),txt(835,513,'描述：外观完好，支持线下当面交易。',17),txt(835,560,'联系方式：卖家同意预约后可见',16,MUTED),button(835,625,490,'发起预约')]
end(s,'04_商品详情预约.svg')

s=frame('预约结果与状态冲突','买家',active='我的预约',note='重复提交、他人抢先与旧页面状态变化应分别提示')
s += [rect(75,220,610,520,'#FFFFFF',20,'#DCE7EF'),pill(112,260,'预约成功',fill='#E7F5EC',color='#28774B'),txt(112,350,'已为你创建待确认预约',27,INK,700),txt(112,410,'卖家将在24小时内同意或拒绝。',18,MUTED),txt(112,455,'联系方式暂不展示。',18,MUTED),button(112,575,240,'查看我的预约'),rect(725,220,640,520,'#FFFFFF',20,'#DCE7EF'),pill(765,260,'状态冲突',fill='#FFF0E5',color='#A96523'),txt(765,352,'商品状态已变更，当前无法预约',24,INK,700),txt(765,408,'旧页面操作已被拒绝；请刷新页面。',18,MUTED),txt(765,460,'他人抢先：手慢了，商品已被他人预约',17,'#B45D45'),txt(765,505,'本人重复点击：提示已预约成功，不新建记录',17,MUTED),button(765,575,240,'刷新商品详情')]
end(s,'05_预约结果与冲突.svg')

# 06: seller publish and own listings
s=frame('发布商品草稿','卖家',active='发布商品',note='草稿可删除；提交后待审核；已发布商品改价或描述须重审')
s += [rect(55,215,1330,570,'#FFFFFF',20,'#DCE7EF'),field(95,270,'商品标题','例：九成新机械键盘',565),field(700,270,'分类','数码 / 图书 / 家居',565),field(95,380,'价格','¥ 180',565),field(700,380,'交易方式','线下当面交易',565),field(95,495,'商品描述','填写成色、瑕疵和交付说明',1170),button(95,660,220,'保存草稿','#E5EFF6',BLUE),button(335,660,250,'提交管理员审核')]
end(s,'06_发布草稿.svg')

s=frame('我的商品','卖家',active='我的商品',note='按状态筛选自有商品；预约中禁止修改或主动下架')
s += [pill(55,215,'全部'),pill(175,215,'草稿'),pill(295,215,'待审核'),pill(415,215,'已发布'),pill(535,215,'已预约'),pill(655,215,'待交易'),pill(775,215,'已售出'),rect(55,280,1330,160,'#FFFFFF',16,'#DCE7EF'),txt(90,335,'机械键盘 · 九成新',22,INK,700),pill(900,304,'已发布'),txt(90,388,'¥180  ·  可编辑、主动下架',17,MUTED),button(1120,335,200,'编辑商品'),rect(55,465,1330,160,'#FFFFFF',16,'#DCE7EF'),txt(90,520,'设计书籍套装',22,INK,700),pill(900,489,'已预约'),txt(90,573,'¥75  ·  已有买家预约，编辑和主动下架已禁用',17,MUTED),button(1120,520,200,'处理预约','#A9B8C5','#FFFFFF'),rect(55,650,1330,115,'#FFFFFF',16,'#DCE7EF'),txt(90,710,'桌面台灯  ·  已售出  ·  历史记录保留',19,INK,600)]
end(s,'07_卖家商品管理.svg')

# 08: reservation lifecycle
s=frame('我的预约与交易','买家',active='我的预约',note='待确认、待交易、已取消和历史预约分别展示')
s += [rect(55,220,1330,190,'#FFFFFF',18,'#DCE7EF'),pill(95,255,'待确认'),txt(95,328,'机械键盘 · 卖家尚未处理',22,INK,700),txt(95,372,'距自动取消还有 18 小时；联系方式隐藏',17,MUTED),button(1120,288,200,'取消预约','#E5EFF6',BLUE),rect(55,435,1330,190,'#FFFFFF',18,'#DCE7EF'),pill(95,470,'待交易',fill='#E7F5EC',color='#28774B'),txt(95,545,'设计书籍套装 · 卖家已同意',22,INK,700),txt(95,590,'电话：仅当前买卖双方可见  ·  线下当面交易',17,MUTED),button(1100,505,220,'取消交易','#E5EFF6',BLUE),txt(70,710,'历史记录：已拒绝 / 已取消 / 超时取消 / 已完成；不可物理删除。',18,MUTED)]
end(s,'08_预约交易.svg')

s=frame('买家预约处理','卖家',active='我的商品',note='只有卖家能同意、拒绝与确认线下交易完成')
s += [rect(55,220,1330,190,'#FFFFFF',18,'#DCE7EF'),pill(95,255,'待确认'),txt(95,328,'机械键盘 · 买家等待你处理',22,INK,700),txt(95,372,'请在预约发起后24小时内处理；双方联系方式仍隐藏',17,MUTED),button(910,290,180,'拒绝预约','#E5EFF6',BLUE),button(1110,290,210,'同意预约'),rect(55,435,1330,190,'#FFFFFF',18,'#DCE7EF'),pill(95,470,'待交易',fill='#E7F5EC',color='#28774B'),txt(95,545,'桌面台灯 · 线下交易进行中',22,INK,700),txt(95,590,'联系信息仅双方可见；实际钱货交接在线下完成',17,MUTED),button(900,505,180,'取消交易','#E5EFF6',BLUE),button(1100,505,220,'确认交易完成'),txt(70,710,'商品进入已售出后留在卖家主页；历史预约保留。',18,MUTED)]
end(s,'08b_卖家处理预约.svg')

# 09: report and notifications
s=frame('商品举报与站内通知','买家',active='发现',note='举报必须填写原因；处理结果在站内显示')
s += [rect(55,225,620,540,'#FFFFFF',18,'#DCE7EF'),txt(95,280,'举报商品',26,INK,700),txt(95,340,'商品：机械键盘 · 九成新',18),field(95,390,'举报原因','请描述违规情况',500),button(95,570,260,'提交举报'),rect(715,225,670,540,'#FFFFFF',18,'#DCE7EF'),txt(760,280,'站内通知',26,INK,700),pill(760,335,'处理完成',fill='#E7F5EC',color='#28774B'),txt(760,405,'你的商品举报已核实，商品已违规下架。',19),txt(760,452,'若举报驳回，也会显示相应处理结果。',16,MUTED),txt(760,595,'通知渠道是乙方设计范围限定。',15,MUTED)]
end(s,'09_举报与通知.svg')

# 10: admin review, stats, audit
s=frame('管理员审核与违规处理','管理员',active='管理后台',note='只有管理员可审核商品和举报；强制下架需联动取消有效预约')
s += [rect(55,225,1330,180,'#FFFFFF',18,'#DCE7EF'),pill(90,258,'待审核'),txt(90,330,'机械键盘 · 价格 ¥180 · 卖家 10032',21,INK,700),button(930,295,180,'审核通过'),button(1130,295,180,'驳回','#E8EFF4',BLUE),rect(55,435,1330,225,'#FFFFFF',18,'#DCE7EF'),pill(90,470,'举报待处理',fill='#FFF0E5',color='#A96523',w=136),txt(90,550,'举报原因：疑似商品描述与实物不符',21,INK,600),txt(90,600,'当前商品：待交易  ·  如确认违规将取消有效预约并隐藏联系方式',16,MUTED),button(930,530,180,'驳回举报','#E8EFF4',BLUE),button(1130,530,180,'违规下架','#BD5F50'),txt(60,760,'普通用户访问本页：拒绝并提示权限不足。',17,MUTED)]
end(s,'10_管理员审核.svg')

s=frame('统计与操作日志','管理员',active='管理后台',note='每日新上架按审核通过日；在售仅统计当前已发布')
s += [rect(55,220,405,180,'#FFFFFF',18,'#DCE7EF'),txt(90,275,'今日新上架',18,MUTED),txt(90,355,'24',44,BLUE,700),rect(490,220,405,180,'#FFFFFF',18,'#DCE7EF'),txt(525,275,'数码类当前在售',18,MUTED),txt(525,355,'38',44,BLUE,700),rect(925,220,460,180,'#FFFFFF',18,'#DCE7EF'),txt(960,275,'图书类当前在售',18,MUTED),txt(960,355,'17',44,BLUE,700),rect(55,440,1330,350,'#FFFFFF',18,'#DCE7EF'),txt(95,495,'关键操作日志',25,INK,700),field(95,520,'商品编号','输入商品编号',330),field(455,520,'操作人','用户 ID / SYSTEM',330),field(815,520,'时间范围','选择起止时间',330),button(1170,533,155,'查询'),txt(95,675,'2026-10-31  预约超时取消   SYSTEM   商品 #P1209   已预约 → 已发布',17,INK),txt(95,725,'2026-10-31  管理员审核通过   ADMIN01   商品 #P1188   待审核 → 已发布',17,INK)]
end(s,'11_管理统计日志.svg')

print('SVG boards:',len(list(ROOT.glob('*.svg'))))
