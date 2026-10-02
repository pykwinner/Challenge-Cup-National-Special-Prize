# -*- coding: utf-8 -*-
"""生成本节主收件回执（_receipts_main.jsonl）。只登记本次实际下载到本地的公开件。"""
import json, hashlib, os, datetime

BASE = r"C:/Users/13349/Desktop/小挑/补充数据_20261002/13_热泵机组样本"
TZ = datetime.timezone(datetime.timedelta(hours=8))


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def mt(p):
    ts = os.path.getmtime(p)
    return datetime.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


ITEMS = [
    dict(rel="sources/纽恩泰/新河县滏阳中学取暖改造_纽恩泰NERS-G60KD_采购合同.pdf",
         url="http://www.ccgp-hebei.gov.cn/ContractAnncFiles/7e81c2e2-fecc-426e-aa1c-3b6d637f2b1e.pdf",
         status=200,
         note="河北省政府采购网 采购合同附件；含纽恩泰 NERS-G60KD 产品技术参数表，4 个制热工况点（7/45、-12/41、-12/55、-20/41）。合同签订日 2023-10-26。"),
    dict(rel="sources/CQC/CQC_2022-04-25_41938_低环境温度空气源热泵冷水机组_认证名录.pdf",
         url="https://www.cqc.com.cn/www/upload/files/upload/resources/file/2022/04/25/41938.pdf",
         status=200,
         note="CQC 认证名录多品类合册，pp.52-61 为低环境温度空气源热泵（冷水）机组条目；只有制造商+型号+额定出水温度（35/41/55℃），无制热量/功率/COP。"),
    dict(rel="sources/CQC/CQC_2024-04-23_60465_海悟等.pdf",
         url="https://www.cqc.com.cn/www/upload/files/upload/resources/file/2024/04/23/60465.pdf",
         status=200,
         note="CQC 认证名录另一册；经逐页查为显示器等品类，未见热泵性能值。留作 CQC 名录不含性能值的证据。"),
    dict(rel="sources/CRAA/CRAA_2022-11_制冷空调产品认证名录_含产品性能.pdf",
         url="http://www.chinacraa.org/upload/202211/07/202211071028347309.pdf",
         status=200,
         note="中国制冷空调工业协会 认证名录；表头'产品性能'是认证类别（区别于监督认证/领跑者认证），不是性能数值。含海悟、山东宏力、东营熙成等低环温空气源热泵厂型号。"),
    dict(rel="sources/乌鲁木齐项目/九家湾空气源热泵试点项目_招标公告.html",
         url="https://ggzy.xinjiang.gov.cn/xinjiangggzy_new/jyxx/001001/001001001/20260903/a5b46aa6-c649-4f18-b6de-1628cc0e9fb9.html",
         status=200,
         note="乌鲁木齐热力（集团）2026 年九家湾空气源热泵试点：购置并安装 4 台 -30℃ 超低温空气源热泵及配套设备 + 2×150m DN250 管网，标段估算价 445.0 万元；未列机组 kW 明细。"),
    dict(rel="sources/武汉与水源热泵/东营_三级水源热泵机组_设备表.pdf",
         url="http://www.dyedz.gov.cn/module/download/downfile.jsp?classid=0&filename=c34bbd6d193a4fb9a6e9400732095346.pdf",
         status=200,
         note="山东东营海上风电大部件产业园地热能源站 主要设备一览表；二级/三级离心式水源热泵给出制热量+源侧进出水温+热水侧进出水温+输入功率。非三城项目，作水源热泵参照。"),
    dict(rel="sources/标准与导则/四川省空气源热泵工程技术标准_征求意见稿.pdf",
         url="http://jst.sc.gov.cn/scjst/c101428/2024/1/29/4870a4620797468e8307ed196207a874/files/四川省空气源热泵工程技术标准 (征求意见稿).pdf",
         status=200,
         note="四川省住建厅 征求意见稿；表3.3.8 给非低环温空气源热泵按名义制热量(≤35/>35kW)×额定出水温度(35/41/50℃)的名义制热 COPh 与融霜 COPdh 限值；3.3.6 要求严寒/寒冷地区选低环温机组。"),
    dict(rel="sources/标准与导则/chinarefac_2024_CO2热泵机组标准.pdf",
         url="http://www.chinarefac.com/upload/202409/23/202409231047343862.pdf",
         status=200,
         note="《二氧化碳空气源热泵机组》编制说明（征求意见稿）；附件2 p55 给编制组调研多厂家 CO2 机组的 环境温度×供水温度→COP 汇总（含 -25/-35/-45℃ 与 55/85℃ 供水），是本次唯一能覆盖 75℃ 供水档的二维数据。非单一品牌。"),
    dict(rel="sources/宁夏固原清洁取暖/原州区炭山乡2024清洁取暖_招标公告.html",
         url="https://ggzyjy.fzggw.nx.gov.cn/dzjy/001001/001001002/001001002001/20250115/8b80b27b93deb2940194692ac57c06e1.html",
         status=200,
         note="固原原州区炭山乡 2024 清洁取暖：空气源热泵 3P 机 + 热风机 147 户 / 预算 147 万元（合同期含供货及安装调试）；公告未含技术参数，正文写'详见招标文件'。"),
]


def main():
    out = []
    for it in ITEMS:
        ap = os.path.join(BASE, it["rel"])
        if not os.path.exists(ap):
            print("MISSING", ap)
            continue
        out.append({
            "url": it["url"],
            "fetched_at_shanghai": mt(ap),
            "http_status": it["status"],
            "sha256": sha(ap),
            "local_path": ap.replace("/", "\\"),
            "note": it["note"],
        })
    p = os.path.join(BASE, "sources", "_receipts_main.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print("wrote", p, len(out))
    for o in out:
        print(o["http_status"], o["sha256"][:12], os.path.basename(o["local_path"]))


if __name__ == "__main__":
    main()
