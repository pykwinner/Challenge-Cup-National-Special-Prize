# -*- coding: utf-8 -*-
"""汇总本 agent 直接取得的样本/招标数据行 -> 大品牌.csv
数值全部逐字来自 sources/大品牌/ 下已下载文件；未印 COP 的行留空，不做任何推算。
"""
import csv, os

HEAD = ['品牌','型号','机组类型','额定制热量_kW','源侧温度_C','出水温度_C','制热量_kW',
        '输入功率_kW','COP','数据性质(样本/检测报告/能效备案/招标门槛)','适用城市','年份','来源文件','source_url']
rows = []

# ================= 天加 TICA =================
TICA_FILE = 'TICA_VForce变频模块机_EN.pdf'
TICA_URL  = 'https://dec.com.pk/assets/images/products/tica/3-Inverter-modular-chiller.pdf'

# --- p.14 Performance Data 名义工况（条件见 p.14 Notes，逐字） ---
nom = [('TCAV035BHE','41','-12','24.0','10.2','2.35'),
       ('TCAV035BHE','45','7','34.0','10.5','3.24'),
       ('TCAV065BHE','41','-12','48.0','20.5','2.34'),
       ('TCAV065BHE','45','7','75.0','23.4','3.20')]
for m,w,a,c,p,cop in nom:
    rows.append(['天加',m,'风冷模块式变频冷水(热泵)机组','','',w,c,p,cop,'样本','武汉','',
                 TICA_FILE+' (p.14 Performance Data; 名义制热工况: 出水%s℃/环境%s℃ 干球)'%(w,a), TICA_URL])

AMBIENT = ['-26','-20','-15','-10','-5','0','7','10','15','20','25','30','35','48','55']
G035 = {
'30':['16.0/8.1','20.0/8.7','24.0/9.2','26.9/9.0','30.5/9.0','34.0/8.5','35.7/8.5','40.0/8.8','40.5/9.0','40.1/7.8','39.8/6.7','42.9/6.4','46.1/6.5','49.0/5.9','51.0/6.1'],
'35':['15.9/9.0','19.8/9.1','23.7/9.7','26.9/9.9','30.1/10.1','33.0/9.3','34.3/9.0','39.2/9.1','40.4/9.1','40.1/7.9','39.7/6.7','42.8/6.4','46.0/6.5','49.0/5.6','51.0/5.8'],
'40':['15.5/10.2','19.6/9.7','23.4/10.8','26.9/11.0','30.3/11.2','32.8/10.1','33.6/9.5','38.9/10.0','40.4/10.4','39.0/9.1','37.5/7.9','40.4/7.6','43.3/7.7','43.5/6.7','45.5/6.9'],
'45':['','19.3/11.6','22.6/11.9','26.3/12.1','29.9/12.3','32.2/11.2','34.0/10.5','38.5/11.1','40.4/11.6','39.5/9.4','38.6/7.3','41.6/7.0','44.5/7.1','44.8/6.2','46.8/6.4'],
'50':['','19.2/13.3','21.8/13.5','25.7/13.5','29.5/13.4','31.8/12.2','32.4/11.6','38.2/12.2','40.3/12.9','38.4/10.7','36.5/8.5','39.3/8.2','42.1/8.3','42.1/8.1','44.1/8.3'],
'55':['','','','','','31.3/12.0','32.0/11.1','38.0/12.6','40.3/14.1','37.8/11.9','35.4/9.6','38.1/9.4','35.4/9.5','36.2/7.2','37.0/7.3'],
}
G065 = {
'30':['31.2/15.9','39.0/16.5','44.7/16.8','50.2/17.3','59.2/17.9','67.8/18.3','75.9/18.4','81.0/18.5','81.8/18.2','81.9/16.5','65.0/11.5','70.2/11.0','75.4/11.2','80.1/10.2','82.1/10.4'],
'35':['30.9/17.7','38.5/18.4','44.7/18.3','49.7/18.8','59.2/19.6','67.2/20.1','75.9/19.9','80.8/20.5','81.8/20.0','82.6/17.6','65.7/12.6','70.9/12.1','76.1/12.3','81.1/10.6','83.1/10.8'],
'40':['31.0/19.4','37.6/20.2','44.9/19.8','49.2/20.8','59.2/21.3','66.1/21.9','75.9/21.4','80.6/22.1','81.8/21.8','81.3/18.9','67.9/13.9','73.1/13.4','78.3/13.6','78.6/11.8','80.6/12.0'],
'45':['','36.6/22.0','44.7/21.6','48.6/23.0','58.9/23.8','65.5/23.7','75.0/23.4','80.4/23.6','81.8/23.5','82.0/20.1','68.1/15.1','73.3/14.6','78.5/14.8','79.0/12.8','81.0/13.0'],
'50':['','','45.2/23.6','49.7/25.2','58.9/26.1','65.0/25.5','73.8/25.1','80.1/25.8','81.9/25.3','80.7/21.4','67.6/16.4','72.8/15.9','78.0/16.1','78.1/13.8','80.1/14.0'],
'55':['','','','','','65.0/27.5','73.8/26.7','79.9/27.4','81.6/27.4','78.1/22.7','67.1/17.7','72.3/17.2','67.1/17.4','78.0/14.8','80.0/15.0'],
}
for m,G in (('TCAV035BHE',G035),('TCAV065BHE',G065)):
    for w in sorted(G,key=int):
        for a,cell in zip(AMBIENT,G[w]):
            if not cell: continue
            c,p = cell.split('/')
            rows.append(['天加',m,'风冷模块式变频冷水(热泵)机组','',a,w,c,p,'','样本','武汉','',
                         TICA_FILE+' (p.15 制热量修正表 Heating Capacity Correction; 表头环境温度第14列原文印作"48")',
                         TICA_URL])

# ================= 美的 Midea =================
# 经销商页面逐字转载厂家参数（已直接抓取页面核对）
M = [('DN-Y750/NSN1-H','75','21.30','3.52',' https://www.zymidea.com/pd.jsp?id=63'),
     ('DN-Y750/NSN1-H','49.30','20.60','2.39',' https://www.zymidea.com/pd.jsp?id=63'),
     ('DN-Y1100/NSN1-H','110.00','33.95','3.24',' https://www.zymidea.com/pd.jsp?id=62'),
     ('DN-Y1100/NSN1-H','70.00','29.54','2.37',' https://www.zymidea.com/pd.jsp?id=62'),
     ('DN-Y1400/NSN1-H','140.00','46.67','3.00',' https://www.zymidea.com/pd.jsp?id=61'),
     ('DN-Y1400/NSN1-H','90.00','38.30','2.35',' https://www.zymidea.com/pd.jsp?id=61')]
for m,c,p,cop,u in M:
    src = '美的%s经销商页_zymidea.com.html' % m.split('/')[0]
    rows.append(['美的',m,'商用低温空气源热泵(烈焰定频系列)','','','',c,p,cop,'样本','乌鲁木齐','',
                 src+' (经销商页逐字转载厂家参数; 页面未注明额定/名义工况出水温度, 故出水温度栏留空)', u.strip()])

rows.append(['美的','RSJ-VE400/MSN1-8R0(E2)','商用空气源热泵热水机组','40.90','','','40.90','9.10','','招标门槛','武汉','2023',
             '美的_广东政采_分项报价表.pdf (清远一中热水项目分项报价表; 环境运行范围-15~46℃, 出水默认50℃可调20~55℃)',
             'https://gdgpo.czt.gd.gov.cn/gpx-bid-file/441801/gpx-bidcore/2023/07/25/8a7eab5d89178ebb01891b4ae9754f04/fxbj8a7eab5d89178ebb01891b4ae9754f042c90f7b873412255017352aacff912df.pdf'])

# ================= 海尔 Haier =================
rows.append(['海尔','KD78 II-NCDF5AU1','商用超低温空气源热泵机组','','','','51','11.1','','招标门槛','乌鲁木齐','2024',
             '海尔_辽宁_分项价格表.pdf (西和县公安局空气能项目分项价格表; ★名义制热量51kW/★名义制热消耗功率11.1kW; IPLV(H)3.25W/W)',
             'http://www.lnsggzyjy.cn/EpointWebBuilder/WebbuilderMIS/attach/downloadZtbAttach.jspx?attachGuid=aae2b85a-ae4f-4a5a-9079-e2e7b25b5a96&appUrlFlag=lnsggzyjy&siteGuid=7eb5f7f1-9041-43ad-8e13-8fcb82ea831a'])
rows.append(['海尔','KD78 II-NCDF5AU1','商用超低温空气源热泵机组','','','','78','24','','招标门槛','乌鲁木齐','2024',
             '海尔_辽宁_分项价格表.pdf (★9.制热工况: 制热量78kW 制热消耗功率24kW, 需提供厂家检测报告; -30℃可正常运行)',
             'http://www.lnsggzyjy.cn/EpointWebBuilder/WebbuilderMIS/attach/downloadZtbAttach.jspx?attachGuid=aae2b85a-ae4f-4a5a-9079-e2e7b25b5a96&appUrlFlag=lnsggzyjy&siteGuid=7eb5f7f1-9041-43ad-8e13-8fcb82ea831a'])
rows.append(['海尔','KD160-NcPB5-U1','60P超低温变频空气源热泵机组','','','','>=102','<=42.1','','招标门槛','乌鲁木齐','2025',
             'Haier古交职教中心结果公告_prec.sxzwfw.gov.cn.html (★招标门槛: 名义制热量(-12℃)>=102kW, 名义制热功率<=42.1kW, 一级能效, 环境-35~45℃)',
             'https://prec.sxzwfw.gov.cn/jyxxzczb/1006259.jhtml'])
rows.append(['海尔','KFXRS-24IIF7AU1','商用空气源热泵热水机组','','','','23.75','4.86','4.93','招标门槛','','2024',
             '海尔_海南政采_报价明细表.pdf (功率4.86kW; COP4.93; ▲实测工况制热量23.75kW; 产水量516L/h; 全年制热能源消耗效率4.43)',
             'https://ccgp-hainan.gov.cn/gpx-bid-file/tenant/460000004/2024/11/14/project/gpx-template/8a1d1edf9798c8c40197b0946ed13400.pdf'])
rows.append(['海尔','KFXRS-42IIF7AU1','商用空气源热泵热水机组','','','','41.687','9.17','4.45','招标门槛','','2024',
             '海尔_海南政采_报价明细表.pdf (功率9.17kW; COP4.45; ▲实测工况制热量41.687kW; 产水量903L/h; 全年制热能源消耗效率4.01)',
             'https://ccgp-hainan.gov.cn/gpx-bid-file/tenant/460000004/2024/11/14/project/gpx-template/8a1d1edf9798c8c40197b0946ed13400.pdf'])

# --- 美的官方 EU 产品参数表（Product fiche，Pdh/COPd 随环境温度变化） ---
import json as _json
_eu = _json.load(open('sources/大品牌/_midea_eu_final.json', encoding='utf-8'))
_EUFILE = {'美的_欧盟产品参数表_Mars-MD25IU_EN.pdf': '美的_欧盟产品参数表_Mars-MD25IU_EN.pdf',
           '美的_AquaThermalSuper_ProductFiche_DE.pdf': '美的_AquaThermalSuper_ProductFiche_DE.pdf'}
for r in _eu:
    if r['page'] not in (3, 4) or r['cop'] <= 1:
        continue          # 仅收录页面内含明确 "low/medium temperature application" 水温度分节的行
    rows.append(['美的', r['model'], '空气源热泵采暖机组(EU Product fiche)', '', str(r['amb']), r['water'],
                 str(r['pdh']), '', str(r['cop']), '样本', '', '',
                 _EUFILE[r['file']] + ' (第%d页 %s condition (%d℃), 出水%s℃分节; 原表给 Pdh/COPd, 未印输入功率故留空)'
                 % (r['page'], r['cond'], r['amb'], r['water']),
                 'https://mbt.midea.com/content/dam/midea-aem/mbt/Mars-18-22MD25IU-109A-EN.pdf'])

# ================= 清华同方 =================
rows.append(['清华同方','FS-L-R-60D','模块式低环境风冷冷水(热泵)机组','','','45','70','19.9','','样本','乌鲁木齐','',
             '清华同方FS-L-R-60D产品选型页_huaruizhicheng.com.cn.html (注1:环境7℃干球/6℃湿球, 供回水45/40℃; 页面cop栏印3.05=制冷62/20.3, 非制热COP, 故COP栏留空)',
             'http://www.huaruizhicheng.com.cn/t_chanpintuijie/productdetails.aspx?p_id=%2b41'])

# ================= 合并并行分头抓取的部件 =================
def merge_part(path, drop_models=(), fix_cop_note=None):
    import os as _os
    if not _os.path.exists(path):
        print('  (skip, not found)', path); return
    n = 0
    with open(path, encoding='utf-8-sig', newline='') as fh:
        rd = csv.reader(fh); next(rd, None)
        for r in rd:
            if len(r) < len(HEAD): r = r + [''] * (len(HEAD) - len(r))
            r = r[:len(HEAD)]
            if r[1] in drop_models: continue
            if not (r[6].strip() or r[7].strip() or r[8].strip()):
                continue          # 任务要求: 抽不到具体数值的型号不要写进来
            if fix_cop_note and (r[1], r[5], r[7]) in fix_cop_note:
                r[8] = ''                      # 该行 COP 印的是制冷COP，置空
                r[12] = r[12] + ' [COP栏留空: 原页cop=3.05对应制冷62/20.3, 非制热COP]'
            rows.append(r); n += 1
    print('  merged %d rows from %s' % (n, path))

# 盾安 / 清华同方（并行抓取件）—— 剔除与上面重复的 FS-L-R-60D 行（其 COP 需要修正）
merge_part('tables/_parts/dunan_tongfang_raw.csv',
           drop_models=('FS-L-R-60D',))
# 格力（并行抓取件）
merge_part('tables/_parts/gree_main.csv')
merge_part('tables/_parts/gree_raw.csv')

# ================= 去重后写出 =================
_seen = set(); _uniq = []
for r in rows:
    k = (r[0], r[1], r[4], r[5], r[6], r[7], r[8], r[12])
    if k in _seen: continue
    _seen.add(k); _uniq.append(r)
if len(_uniq) != len(rows):
    print('  dedup: %d -> %d' % (len(rows), len(_uniq)))
rows = _uniq

# ================= 写出 =================
os.makedirs('tables/_parts', exist_ok=True)
with open('tables/_parts/大品牌.csv','w',encoding='utf-8-sig',newline='') as f:
    wtr = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
    wtr.writerow(HEAD)
    for r in rows:
        assert len(r)==len(HEAD), r
        wtr.writerow(r)
print('main rows:', len(rows))
