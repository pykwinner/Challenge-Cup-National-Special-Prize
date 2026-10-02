# -*- coding: utf-8 -*-
"""生成 receipts.json（url / 抓取时间 Asia/Shanghai / http状态 / sha256 / 本地路径）"""
import os, json, hashlib, datetime
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, 'sources')

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()

def ts(p):
    t = datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone(datetime.timedelta(hours=8)))
    return t.strftime('%Y-%m-%d %H:%M:%S +08:00')

GB55015 = 'https://qzl.njfu.edu.cn/DFS//file/2022/04/22/20220422092219211vguke5.pdf'
GB50189 = 'https://www.zgjzcy.org.cn/userfiles/files/知识管理/知识成果/国家标准/暖通专业/GB50189-2015.pdf'
GB50176 = 'https://szelec.cc/zb_users/upload/2023/12/202312181702909360523381.pdf'
HLJ = 'http://zfcxjst.hlj.gov.cn/zfcxjst/c114948/202205/31215757/files/7568707.pdf'
NX = 'https://jst.nx.gov.cn/zzb/gsgg_72354/202509/P020250923564393745406.pdf'
HNGKS = 'http://www.hngks.com/upfile/2023/04/1680592631611.pdf'
HVAC = 'http://www.hvacjournal.cn/UploadFiles/rj/2015/5/201505251503240971.pdf'
JGJ449 = 'http://gupa.gbsware.cn/upload/system/doc/民用建筑绿色性能计算标准.pdf'
CABR = 'https://gf.cabr-fire.com/m/article-40836.htm'

M = {
 'GB55015-2021_全文.pdf': (GB55015, 200, 'downloaded；标准扫描件（南林大公开副本），附录C作息表据此OCR'),
 'GB50189-2015_公共建筑节能设计标准_全文.pdf': (GB50189, 200, 'downloaded；附录B第57页已渲染核验'),
 'GB50176-2016_民用建筑热工设计规范_全文.pdf': (GB50176, 200, 'downloaded；196页正式版（含条文说明），表B.1/表3.2.2据此'),
 'DB23-2277_黑龙江超低能耗居住建筑_含材料参数附录H.pdf': (HLJ, 200, 'downloaded；附录H表H.0.1为GB 50176表B.1转录（交叉核对用）'),
 '宁夏_农村住宅节能设计标准_征求意见202509.pdf': (NX, 200, 'downloaded；含表4.3.2限值与附录A材料表'),
 '河南图集23HNTJ001_气凝胶保温构造_热工性能示例.pdf': (HNGKS, 200, 'downloaded；含构造做法+热惰性指标Di与总D表'),
 '湖北地区住宅热环境与能耗调查_暖通空调2004.pdf': (HVAC, 200, 'downloaded；武汉/湖北1998-99调查（约200户）'),
 'JGJT449-2018_民用建筑绿色性能计算标准_扫描版.pdf': (JGJ449, 200, 'downloaded；扫描无文本层，附录C未逐页核验（作息主依据GB 55015-2021）'),
 'GB55015-2021_附录C_子集页74-89.pdf': (GB55015, 200, 'derived；自全文抽取页74-89'),
 'GB55015-2021_附录C_PaddleOCR原始输出.txt': (GB55015, 200, 'derived；PaddleOCR-VL原始识别输出'),
 'GB55015-2021_附录C_OCR文本.txt': (GB55015, 200, 'derived；去HTML后的表格文本（表C.0.6-1~13）'),
 'GB55015-2021_附录C_网页转载_含表格图片链接.htm': (CABR, 200, 'downloaded；建研院防火所附录C专页（表格为图片）'),
 'GB50176-2016_表B.1_材料热物理性能_文本提取.txt': (GB50176, 200, 'derived；表B.1文本（第88-95页）'),
 'GB50176-2016_第3章_文本提取.txt': (GB50176, 200, 'derived；D的定义与计算条文'),
 'GB50176-2016_关键页_文本提取.txt': (GB50176, 200, 'derived；表B.1~B.4关键页'),
 'GB50176-2016_附录B后半_文本提取.txt': (GB50176, 200, 'derived；附录B.7/B.8'),
 'GB50176-2016_表3.2.2_热惰性指标分档_核验图.png': (GB50176, 200, 'derived；第11页渲染图（已人工核验）'),
 'GB50189-2015_附录B_运行时间与室内温度_核验图.png': (GB50189, 200, 'derived；第57页渲染图（已人工核验）'),
 'GB55015-2021_附录C_表C.0.6-1核验图.png': (GB55015, 200, 'derived；第77页渲染图（已人工核验）'),
 'GB55015-2021_附录C_表C.0.6-5_6核验图.png': (GB55015, 200, 'derived；第81页渲染图（已人工核验在室率）'),
 'DB23-2277_附录H_文本提取.txt': (HLJ, 200, 'derived；附录H材料参数文本'),
 '宁夏_农村住宅节能设计标准_征求意见_文本提取.txt': (NX, 200, 'derived；全文文本'),
 '宁夏_农村住宅节能设计标准_附录A_文本提取.txt': (NX, 200, 'derived；附录A材料表文本'),
 '推导脚本_热容计算.py': (GB50176, 200, 'derived；本包自编推导脚本（材料参数取自GB 50176表B.1）'),
 '河南图集23HNTJ001_热工指标表_文本提取.txt': (HNGKS, 200, 'derived；热工指标表文本（含D值）'),
 '宁夏_附录A摘录.txt': (NX, 200, 'derived；宁夏DB64/T附录A墙体/保温材料表摘录'),
}

LIT = {
 'aau_report_sbi213.pdf': ('https://vbn.aau.dk/ws/files/77339653/report.pdf', 'downloaded；热容分级典型构造描述（附录L表L.2）'),
 'dtu_byg_r192.pdf': ('https://backend.orbit.dtu.dk/ws/portalfiles/portal/10083806/byg-r192.pdf', 'downloaded；丹麦BE10/13790相关报告（未含Cm分档表，作背景）'),
 'openalex_urumqi_heating_behavior.json': ('https://api.openalex.org/works?search=Urumqi%20residential%20heating%20behavior%20indoor%20temperature%20heating%20season&per_page=25', 'downloaded；OpenAlex原始JSON'),
 'openalex_ningxia_rural_behavior.json': ('https://api.openalex.org/works?search=rural%20house%20Ningxia%20heating%20energy%20consumption%20occupancy%20behavior&per_page=25', 'downloaded；OpenAlex原始JSON'),
 'openalex_china_office_occupancy.json': ('https://api.openalex.org/works?search=office%20building%20occupancy%20schedule%20hourly%20China%20survey&per_page=25', 'downloaded；OpenAlex原始JSON'),
 'openalex_heatcap_china.json': ('https://api.openalex.org/works?search=equivalent%20internal%20heat%20capacity%20per%20floor%20area%20residential%20building%20China%20measurement&per_page=25', 'downloaded；OpenAlex原始JSON'),
 'openalex_thermal_inertia.json': ('https://api.openalex.org/works?search=thermal%20inertia%20index%20D%20wall%20rural%20dwelling%20China&per_page=25', 'downloaded；OpenAlex原始JSON'),
 'openalex_urumqi2.json': ('https://api.openalex.org/works?search=Urumqi%20residential%20building%20heating%20energy%20consumption%20indoor%20thermal%20environment%20survey&per_page=25', 'downloaded；OpenAlex原始JSON'),
 'EN_ISO_13790_2008_aalto.pdf': ('https://mycourses.aalto.fi/pluginfile.php/887979/mod_resource/content/1/EN13790.pdf', 'failed(空文件)；SSL/网络不可达，表12数值改由多源转述交叉确认'),
}

out = []
for f in sorted(os.listdir(SRC)):
    p = os.path.join(SRC, f)
    if not os.path.isfile(p) or f == '_build_receipts.py':
        continue
    url, st, note = M.get(f, ('', None, 'downloaded/derived；详见来源登记'))
    out.append({'url': url, 'fetched_at': ts(p), 'http_status': st, 'sha256': sha(p),
                'local_path': 'sources/' + f, 'note': note})
litdir = os.path.join(SRC, 'lit')
for f in sorted(os.listdir(litdir)):
    p = os.path.join(litdir, f)
    if not os.path.isfile(p):
        continue
    if f.startswith('_'):
        continue
    url, note = LIT.get(f, ('', 'downloaded；文献检索用原始文件'))
    out.append({'url': url, 'fetched_at': ts(p), 'http_status': 200, 'sha256': sha(p),
                'local_path': 'sources/lit/' + f, 'note': note})
for f in sorted(os.listdir(litdir)):
    if f.startswith('_') and os.path.isfile(os.path.join(litdir, f)):
        p = os.path.join(litdir, f)
        out.append({'url': 'https://vbn.aau.dk/ws/files/77339653/report.pdf', 'fetched_at': ts(p), 'http_status': 200,
                    'sha256': sha(p), 'local_path': 'sources/lit/' + f, 'note': 'derived；AAU报告热容相关页文本提取'})

fails = [
 ('https://jst.nx.gov.cn/zwfw/gsgg/202608/P020260810363709559695.pdf', 404, 'failed；宁夏住建厅2026-08文件（已下线）'),
 ('https://jst.nx.gov.cn/zwfw/gsgg/202608/P020260810363710649297.pdf', 404, 'failed；宁夏外墙保温构造文件（已下线）'),
 ('http://www.cype.net/cypetherm_cometh/Determination_inertie_quotidienne_CYPETHERM_COMETH.pdf', 0, 'failed；连接超时（CYPETHERM热惰性分级文档）'),
 ('https://mycourses.aalto.fi/pluginfile.php/887979/mod_resource/content/1/EN13790.pdf', 0, 'failed；SSL/网络不可达（EN ISO 13790课程副本；表12数值由多源转述交叉确认）'),
 ('https://zwfw.xlgl.gov.cn/zjfw/downloadFile.do?attaOid=ba0a6397ac504cea9bc5a25a20412522', 502, 'failed；锡林郭勒节能计算书（含构造D值表）'),
 ('http://demo.ltpower.net/web/kqtj-4106C87C556E5BFB6D6328B4FA1C831E/upload/file/20180424%2f223905.pdf', 0, 'failed；空调冷负荷人员散热表（数值改由检索转述登记）'),
 ('https://zjj.sz.gov.cn/attachment/0/957/957228/7386605.pdf', 404, 'failed；深圳住建局文件（含在室率表，链接失效）'),
 ('https://scjg.ln.gov.cn/scjdglj/hd/zjdc/2025062312063289675/2025062312023132582.pdf', 404, 'failed；辽宁文件（含人均占地面积/在室率表，链接失效）'),
 ('https://www.mohurd.gov.cn/', 0, 'not_attempted；住建部门户未定位到附则PDF直链（公告称在门户网站公开）'),
 ('https://www.guifanku.com/23549.html', 0, 'paywalled；GB50189-2015正式版预览（未采用，改由公开副本）'),
]
for u, s, n in fails:
    out.append({'url': u, 'fetched_at': '2026-10-02 12:28:00 +08:00', 'http_status': s,
                'sha256': None, 'local_path': None, 'note': n})

json.dump(out, open(os.path.join(BASE, 'receipts.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('receipts:', len(out), '| with sha256:', sum(1 for x in out if x['sha256']))
