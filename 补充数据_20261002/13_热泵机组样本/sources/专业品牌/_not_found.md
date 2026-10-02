# 没拿到清单 — 专业品牌（纽恩泰 / 海悟 / 芬尼克兹 / 中广欧特斯 / 生能 / 阿尔普尔 / 力诺瑞特）

记录格式：`品牌 | 想找什么 | 试过的 URL | 失败原因`

抓取日期：2026-10-02（北京时间）
网络：国内站直连，海外站经 127.0.0.1:7897 代理；UA 为 Chrome/120。

---

## 一、厂家官网「资料下载 / 样本 PDF」

| 品牌 | 想找什么 | 试过的 URL | 失败原因 |
|---|---|---|---|
| 纽恩泰 | 官网产品样本/选型手册 PDF | `https://www.ne01.com/`、`/product/index.html`、`/product/index_1.html`、`/product/index_3.html`、`/product/index_4.html`、`/product/index_5.html`、`/product/index_7.html`、`/service/index.html`、`/service/information.html`、`/about/news.html`、`/other/service.html` | 全站**零 PDF/样本链接**（逐页 grep `\.pdf|\.doc|下载|资料|样本|手册` 均无命中）。官网只做营销，样本不给下载 |
| 纽恩泰 | 官网域名（旧线索） | `http://www.nuentaicn.com`、`http://www.gdnuentai.com` | **HTTP 502**（两个域名均已失效/不解析到有效站点） |
| 纽恩泰 | 选型系统内的产品性能库 | `http://biserver.ne01.com:8088/lectotypefront/`、`POST http://biserver.ne01.com:8088/lectotype/lectotypeList` | 前端可开，API 也可调（返回 200），但**返回的是用户提交的项目选型记录**（"测试"数据），不是逐工况性能表；另有 `chunk_801.js` 内嵌型号清单，只给单一标称点（如 NERS-G60KD 168kW/45.7kW），**未标工况温度**，未入表 |
| 芬尼克兹 | 中国站样本/下载栏目 | `https://www.phnix.com/` | **HTTP 502**（中文站不可用；英文站 `www.phnix-e.com` 可用） |
| 海悟 | 中文样本/资料下载栏目 | `https://www.haiwu.com/`（首页、`/product/crac/qjny/rs/152.html`） | 首页只列产品页，**无 PDF 下载入口**；已取到英文画册 `https://www.haiwu.com/Upload/en/Haiwu%20Household%20Heat%20Pump.pdf`（即可用样本） |
| 中广欧特斯 | 官网样本 PDF | `https://www.outes.com/`（站点可访问） | 官网可访问但**未挂可下载样本 PDF** |
| 生能 | 官网样本 PDF | `http://www.shengneng.com` | **HTTP 502** |
| 阿尔普尔 | 官网样本 PDF | `http://www.airpower.com.cn` | **HTTP 502**（正确域名应为 `www.airpowercn.com`，已由分派代理继续处理） |
| 力诺瑞特 | 官网样本 PDF | `http://www.linuo-ritt.com` | **HTTP 502** |

## 二、第三方文库（有该样本，但需付费/金币 —— 按规矩不绕）

| 品牌 | 想要的件 | URL | 失败原因 |
|---|---|---|---|
| 纽恩泰 | 《NEWENERGY纽恩泰商用热泵系列产品手册》74 页 | `http://hvacdoc.cn/search/?wd=系列产品` | 下载需 3~5 金币（付费墙） |
| 纽恩泰 | 《纽恩泰产品应用手册.pdf》约 400 页（含商用冷暖产品系列参数） | `https://max.book118.com/html/2023/0602/5032123142010220.shtm` | 在线文档平台，下载需付费/会员 |
| 纽恩泰 | 超低温变频高温复叠热泵样册（NE-AZB45KDY/90KDY，3 页） | hvacdoc.cn | 需金币 |
| 生能 | 《生能空气能渠道冷暖配置选型.pdf》约 40 页 | `https://max.book118.com/html/2024/0410/7145131153006063.shtm` | 需付费/会员 |
| 芬尼克兹 | KV 系列超低温冷暖机、KingKong 系列样册 | `http://hvacdoc.cn/search/?wd=暖空气` | 需金币 |

## 三、认证 / 备案 / 检测名录渠道

| 目标 | 试过的 URL | 失败原因 |
|---|---|---|
| CQC 一证一档（含性能参数） | `https://www.cqc.com.cn/dynamic/contentcore/resource/download?ID=43717`、`ID=40135`、`ID=40683`、`ID=40633`、`ID=60426`（带 `Referer: https://www.cqc.com.cn/` 重试） | **HTTP 404**（5 个 ID 全部 404；该下载口已失效或需登录会话） |
| CQC 认证证书汇编 | `https://www.cqc.com.cn/www/upload/files/upload/resources/file/2022/03/08/41794.pdf`（111 页）、`.../2024/06/03/60810.pdf`（109 页）、`.../2024/04/23/60465.pdf`（100 页） | **下载成功但内容不可用**：只有「制造商 / 产品名称 / 标准 / 证书编号 / 生效-有效期」，**无制热量、输入功率、COP 数值**。已保留 41794、60810、60465 作备案佐证，不入表 |
| CRAA 中国制冷空调工业协会产品性能认证名录 | `http://www.chinacraa.org/upload/202211/07/202211071028347309.pdf`、`http://www.chinacraa.org/upload/202209/06/202209061105562024.pdf` | **下载成功但内容不可用**：只有认证编号与标准号，**无性能数值** |
| 中国能效标识网（能效备案数据库） | `https://www.energylabel.gov.cn/` | **HTTP 000 连接失败**（域名不可达） |
| 合肥通用机电产品检测院 / 国家压缩机制冷设备质检中心 检测报告 | 全网检索 | **无公开可下载的检测报告全文**（报告只随投标文件提交，未公开发布） |

## 四、政府采购 / 招投标附件（下载失败或内容不可用）

| 目标 | URL | 失败原因 |
|---|---|---|
| 青岛 2 包采购需求（3.0P/5.0P 热泵热水机 41℃/50℃ 工况） | `http://www.ccgp-qingdao.gov.cn/sdgp2014/api/file/2024/04/30/3cad62b1-4ba5-499a-a24a-1a9a65ce332b.pdf` 及其 `/2包采购需求.pdf` 变体 | 返回 508 字节 **HTML 反爬页**，非 PDF（两次尝试均如此） |
| 北京市政府采购 技术标书（含 -12℃/41℃、-20℃/41℃ COPh 要求） | `http://www.ccgp-beijing.gov.cn/xxgg/qjzfcggg/qjzbjggg/P020241016539019671961.docx` | **HTTP 404** |
| 巴彦淖尔 低环境温度空气源热泵（按供暖面积 6/11/12.5/16/20 kW 分档） | `http://ggzyjy.bynr.gov.cn/EpointWebBuilder/WebbuilderMIS/attach/downloadZtbAttach.jspx?attachGuid=daafbccb-8efd-4697-b053-1138538beb66&appUrlFlag=ztb001&siteGuid=7eb5f7f1-9041-43ad-8e13-8fcb82ea831a` | **HTTP 502**（直连与代理均失败）；0 字节文件已删除 |
| 纽恩泰 华亭市山寨初中成交公告 —— **内容不可用** | `https://gcycloud.cn/gpx-bid-file/620824/gpx-template/2025/9/2/8a8084dc98bd043e0199090767834553.pdf?accessCode=...` | **下载成功（2 页）但不入表**：给出 NE-G60-AZB180KDA 的 名义制热量120kW / 低温制热量105kW / 名义制热功率46.69kW / COP 2.16 等，**但全文未标注各值的环温与出水温度**，无法定位到坐标轴 |
| 河北保定清苑区电代煤 3P/5P/6P/8P 技术参数 | `http://www.ccgp-hebei.gov.cn/BidWinAnncFiles/3b43aab8-f1b7-44bb-9588-3fb29842cf20.pdf` | **下载成功但不入表**：只给"名义工况"制热量/COP，**未写明环温与出水温度** |
| 内蒙古 巴彦淖尔/临夏/青岛 等分档门槛 | 各地方平台 | 部分已取得（见 CSV，品牌记为「(招标未指定品牌)」）；部分平台 502/反爬 |

## 五、公司招股说明书

| 品牌 | 想要的 | 结果 |
|---|---|---|
| 纽恩泰 | 深交所主板 IPO 招股说明书（申报稿，2026-04-29）逐型号性能表 | **PDF 已取到 445 页**（`http://pdf.dfcfw.com/pdf/H2_AN202604291821755350_1.pdf`），但通篇为财务/法律/行业定性描述，**唯一数值线索**是 p124 案例"新疆喀什伽师县 21 所学校项目，极端最低温 -23.6℃ 时 COP 1.8"（无逐工况表） → **不入表** |
| 芬尼科技（PHNIX 母体） | 同 | **已取到并可用**：`http://static.sse.com.cn/stock/disclosure/announcement/c/202103/000742_20210301_Q6V6.pdf` p138–139 行业对比表，商用采暖行**明确标注"环境 -12/-14℃、水温 36/41℃"**，已抽 5 点入表 |

## 六、其它已排除的来源

| 来源 | 原因 |
|---|---|
| `https://sdhs0001.gys.cn/supply/*`（力诺瑞特超低温机组商品页） | 标题写"超低温专用机组"，但参数栏填的是热水器通用信息（10P、100L 等），**明显与超低温热泵不符，按规矩不引用** |
| `https://product.11467.com/info/2873567.htm`（阿尔普尔 10P 三联供） | 报价值 80000 元/台与 COP 3.50/-15℃ 2.28 出自**商品报价页而非厂家样本或检测报告**，工况未标全，未入表 |
| 各类"煤改电中标明细/分项报价表"（中广欧特斯阿拉善、生能安阳等） | 多只含品牌/型号/单价/数量，**无制热量-功率-COP 三件套**，不入表 |

---

## 七、已取得但**无法在（环温 × 出水温度）平面上定位**，故未入 CSV

这些行的数值是逐字读到的，但原文**没有给温度工况**，放进去会让下游误当成某个工况点，所以只在此留档。

| 品牌 | 型号 | 拿到的数值（逐字） | 出处 |
|---|---|---|---|
| 力诺瑞特 | LPRF-30GW/BP | 常温工况制热量 **3000w**、高温制热COP **2.40**、R410A | 官网 `http://www.linuo-paradigma.com/product_detail/1247964026445123584.html`（表头只写「常温工况」，未标温度数值） |
| 力诺瑞特 | LPRF-40GW/BP | 常温工况制热量 **4000w**、高温制热COP **2.30**、R410A | 同上 |
| 力诺瑞特 | LPR-18ID/CER01 | 制热 IPLV **3.35**（GB 37480-2019）、制冷 IPLV **4.00** | 搜狐 2024HPE 创新奖申报报道 `https://www.sohu.com/a/753553726_241046` |
| 阿尔普尔 | 水气双源三联供大型机组 | 冬季供暖能效 **3.62**；同页另一处写 **3.63**（原文两处不一致，均保留） | 官网 `http://www.airpowercn.com/product/735.html`（未注明环境温度与出水温度） |
| 阿尔普尔 | 水气双源三联供大型机组 | 夏季制冷能效比 **5.42**（**制冷侧**，与采暖工况不可混用） | 同上 |
| 阿尔普尔 | 单机双级螺杆型空气源热泵机组 | **出水温度可达 75℃**、**-35℃ 可靠运行**、R134a | 工信部《国家工业节能技术装备推荐目录(2018)》PDF 第115页（印刷页46）（只有边界值，无制热量/功率/COP） |
| 力诺瑞特 | LPR-9ID/CER01、LPR-15ID/CER01、LPR-18ID/CER01、LPR-24IID/CER02 | 仅 **额定出水温度 41℃** + 证书编号 CQC23701387132/7130/7131 | CQC 名录 PDF `https://www.cqc.com.cn/www/upload/files/upload/resources/file/2023/05/15/43512.pdf` 第46页（**无制热量/功率/COP 数值**） |
| 力诺瑞特 | LPR-18ID/CER01 | 工信部 2024 推荐目录序号41，能效指标栏原文「**优于1级能效**」 | `https://www.miit.gov.cn/cms_files/filemanager/1226211233/attach/20244/e53c2c8eeaf345a296cae6c0a752c135.pdf` 第62页（只有能效等级结论） |
| 力诺瑞特 | LPR-18ID/CER01 | 山东 2026 省级监督抽查**合格**（山东省产品质量检验研究院；检验项目含名义制热量、名义制热消耗功率、名义制热性能系数、低温制热量等） | `http://amr.shandong.gov.cn/art/2026/8/26/art_76502_10317152.html` —— **汇总表只给合格结论，不给实测值**，勿由"合格"反推数值 |
| 阿尔普尔 | —— | **本次省级监督抽查未抽到样品**（"待销产品数量不符合监督抽查实施细则要求"），无实测值可得 | 同上公告 |

另：**能效标识网 `https://www.energylabel.gov.cn/` DNS 解析失败（NXDOMAIN），站点不可达** —— 能效备案数值这条线整体断掉。
另：济南市政府 PDF（阿尔普尔超低温热泵项目，疑似含检测报告 2017LK1101 的 -19℃ COP 2.43、出水 67.17℃、-30℃ 水温 56.44℃）**HTTP 403**（带 Referer/cookie/完整浏览器头、代理与直连均 403），公开原件找不到，**这几个数一律未写入任何产出**。


---

# 附：分派代理的原始未拿到清单（逐条保留）

### ## 附A. 芬尼克兹 PHNIX + 海悟 HAIWU

```
# PHNIX(芬尼克兹) + HAIWU(海悟) 未拿到清单
# 格式: 品牌 | 想找什么 | 试过的 URL | 失败原因
# 生成时间: 2026-10-02

【一、PHNIX 官方中文站整体不可达 —— 未拿到官网中文选型手册/下载中心】
芬尼克兹 | 官网中文站首页与「下载中心/资料下载」栏目 | https://www.phnix.com/ ; http://www.phnix.com/ ; https://phnix.com/ ; https://www.phnix.com/productdetails/post-38.html ; 以及经代理/不经代理两种方式 | HTTP 502 Bad Gateway（nginx 上游故障，持续整场任务未恢复）。域名解析到 119.23.84.165 / yundunwaf1.com（阿里云 WAF）。该站是 WordPress 风格（post-NNNN.html），站点整体宕机，网上可检索到的「超级北极星」产品页只能通过 web.archive.org 快照读取（快照页仅有 HSPF 3.32~3.66 / 一级能效 / 运行环温 -41~43℃ 等表头，无逐工况制热量-功率-COP），故未登记为数据行
芬尼克兹 | 官网 phnix-e.com 的 HTML 站点（产品页/下载页） | https://www.phnix-e.com/ ; /download ; /downloads ; /en/download ; /index.php ; /sitemap.xml ; /robots.txt | 所有 HTML 路径返回 HTTP 200 但 Content-Length: 0（站点前端为空白，只有 /upfiles/*.pdf 直链可用）。/robots.txt 返回 200 仅含 "Disallow: /admin.php"，无 sitemap
芬尼克兹 | 官网 phnix-e.com 的站点地图/下载索引（用于系统性枚举产品 PDF） | http://web.archive.org/cdx/search/cdx?url=phnix-e.com*&filter=urlkey:.*pdf.* | CDX 可查得 53 条 PDF 历史 URL，但绝大多数为泳池机/热水机/风机盘管/新风机画册；23 个 2018 年以后的 PDF 已全部下载逐个开箱检查，其中含采暖工况表的仅 2 份（暖通总本、HeatMaster），其余无采暖工况表

【二、PHNIX 中文市场采暖机（KV系列/KingKong金刚/北极星中文样本）—— 未拿到官方中文选型手册 PDF】
芬尼克兹 | KV 系列超低温冷暖机中文样本 PDF | 中文搜索（百度/必应）若干关键词 + 官网目录枚举 | 检索到的 KV 系列参数仅出现在英文版《2022-2023 暖通总本》第17页（HeatPro Series，型号 KV15A/KV15T/KV24A/KV24T，单一工况 7/6℃ 出 35℃，且原表未印 COP）。未找到中文版 KV 系列分环温选型表
芬尼克兹 | KingKong（金刚）系列中文参数表 | 中文搜索 | 仅检索到定性宣传（-15℃正常制热、最高出水60℃），未检索到任何可下载的参数表文件
芬尼克兹 | 富裕县公安局空气源热泵采购公告《技术指标》原文（含 环温7/-12/-20/-25 × 出水45/41/50 的制热量/输入功率/COP 四档要求） | https://heilongjiang.zhaobiao.cn/free_v_e2d309077180734e7178afaa2fd5c282.html ; https://www.yfbzb.com/inviteBid/detail/20260920_632889033.html | 招标正文中具体温度与参数数值被 "*" 脱敏，且属「招标门槛值」而非机组实测/标称值；未取得未脱敏原始文件，故未登记
芬尼克兹 | 环评报告中引用的《龙江县陆玖矿业超低温变频空气源热泵节能方案书》原件（PHNIX 厂家方案书） | 未找到公开发布地址 | 仅通过黑龙江省生态环境厅公开的环评报告转引（已登记，见 agent_phnix_haiwu.json 中 PASRW700S-PS-BP 各行）。方案书原件本身未公开
芬尼克兹 | PHNIX 第三方检测报告原件（合肥通用机电产品检测院/中国制冷空调工业协会） | https://www.cqc.com.cn/dynamic/contentcore/resource/download?ID=43717 (404) ; ID=40135 ; ID=35983 | CQC 该 ID 已失效（HTTP 404，返回 196 字节 HTML）。未取得含制热量/COP 的检测报告原件

【三、HAIWU 官方站 —— 已转做数据中心温控，中文站无热泵参数表】
海悟 | 官网中文站「清洁能源/户用热泵」产品页的分机型参数表 | https://www.haiwu.com/product/crac/qjny/hy/185.html (户用整体式) ; /product/crac/qjny/hy/143.html (户用分体式) ; /product/crac/qjny/rs/152.html (商用热泵热水) ; /product/crac/qjny/sy/151.html (商用定频喷气增焓) | 页面均为营销文案（文字描述"制热运行最低环温-35℃、最高出水60℃""-25℃环温出水50℃"等），**不含任何制热量/输入功率/COP 数值表**，故未登记数据行。现官网已整体转向数据中心液冷/机房空调
海悟 | 官网中文站 sitemap 里的产品 PDF（资料下载） | https://www.haiwu.com/sitemap.xml（1033 条 URL，已全量下载解析） | 1033 条 URL 中无任何 .pdf；全站 PDF 仅有 4 个，均在 /Upload/en/ 下（About Haiwu.pdf / Haiwu Household Heat Pump.pdf / Haiwu ICT Product-50Hz.pdf / Haiwu ICT Product-60Hz.pdf），其中只有 Household Heat Pump 含热泵参数表
海悟 | 官网中文站「经销商」栏目的产品样本 | https://www.haiwu.com/partners/dealers/ | 仅经销商名录/申请入口，无产品样本下载
海悟 | 海悟备用域名（.com.cn / .cn / haiwukqn） | https://www.haiwu.com.cn/ ; https://www.haiwu.cn/ ; http://www.haiwukqn.com/ ; https://www.haiwukqn.com/ | 全部 DNS 解析失败（curl exit 6，无法解析主机）
海悟 | 中文版家用热泵画册（与英文本同源的中文样本 PDF） | 未找到 URL | 官网 /Upload/en/ 下只有英文本；未找到中文版下载地址

【四、HAIWU 招投标附件 —— 未取得含逐工况参数的附件原文】
海悟 | 青岛市即墨区田横镇 2022/2023 清洁取暖电代煤改造项目 中标公告附件（载有 HLWS008B1C6B / HLWS014B1C6A / HLWS016B1C6A） | https://120.221.95.150/TradeDetals-ZtbShow/121291-5070-1-2-0/dc202cfa-8edd-4b06-85ab-cc1056197350 ; https://ggzy.qingdao.gov.cn/c/TradeDetals-ZtbShow/126907-5070-1-2-0/2a6f65bd-0f40-4055-9585-74ec0cb037e5-B03CE5B6FC1D2D3C2D5FCEBC39AA2406.html ; http://zgggzy.com/jieguo/show.php?itemid=14669 | 120.221.95.150 连接失败（curl exit 0/超时，0 字节）；青岛公共资源交易平台需登录/区块链存证前置校验。公开可见的表格内容只有型号、容量级别(≥5000W/7000W/10000W)与单价，**无制热量/输入功率/COP 字段**
海悟 | 北京怀柔区九渡河镇 2022 煤改电项目 海悟供货机型技术参数表 | 未找到可直接下载的附件 | 仅检索到"名义工况(-12℃)制热量 ≥5.4/9.0/10.8/14.4/18.0 kW"等**招标门槛值**（≥ 要求），按裁定属「招标门槛」而非厂家标称，且未取得原始附件，未登记
海悟 | 河南烟草许昌电能烤房采购项目（含海悟投标业绩） | http://222.143.158.106:8088/ggzy/download.svc?id=2c91e6527a0d0f60017a0ec5a2450fcd | 成功下载 145 页 PDF（HTTP 200），但该项目为「电能烤房」而非采暖热泵，且开箱检查后全文只有投标人名单与报价，**无海悟任何技术参数**，已删除未入库
海悟 | 经销商/第三方产品页上的 HLL 系列（5/10/15/30HP）分环温参数表 | https://hsf-365.com/product_view_444_371.html | HTTP 200，但该页为 JS 前端渲染，返回 HTML 中无 <table> 标签、无 HLL 型号字符串、无参数数值；参数表疑以图片形式存在，未找到数据接口，故未登记（检索摘要中出现的 -12℃/-20℃ 数值无法回指可下载原件，按"不编造"规则一律未采用）

【五、能效备案 —— 未取得分环温能效备案值】
芬尼克兹/海悟 | 中国能效标识备案数据库（能效备案值） | 未找到公开可查的分环温备案数据接口 | 中国能效标识网对分环温 Pdh/COP 备案数据不提供公开逐条查询；本次未取得任何「能效备案」性质的数据行
```

### ## 附B. 中广欧特斯 OUTES + 生能 SHENGNENG

```
未拿到清单 — 中广欧特斯 / 生能
生成时间：2026-10-02 19:08 +08:00
格式：品牌 | 想找什么 | 试过的 URL | 失败原因(http码/404/需登录/无PDF)

====================================================================
一、中广欧特斯（浙江中广电器集团股份有限公司）
====================================================================

中广欧特斯 | 官方商用站商品页（低温强热机组）的逐工况参数表 | https://www.outes.com.cn/ ；https://www.outes.com.cn/proInfo/9.html ；http://outes.com.cn/proInfo/9.html ；https://www.outes.com.cn/index.php/proInfo/9.html | 全部 HTTP 500 "Server Error"（站点自身故障，非封禁）；四条 URL 变体（含 http/https、带不带 www、带不带 index.php）返回内容完全相同（6615 字节错误页）

中广欧特斯 | 官网家用/暖通站产品参数表 | https://www.outes.com/products_page/317.html（暖洋洋 ZGR-28ⅡBDBPG6H）；/316.html；/139.html（双子星Super）；/273.html（境界 ZGR-14ⅠBDBPG9R） | HTTP 200，但页面为纯营销长页（HTML 内 0 个 <table>），参数表不以文本形式存在；页内产品图（20250508/20260615 等 10 张 626×626 PNG、20240814 等 3 张 800×800 PNG）逐张渲染查看后确认均为整机外观照片/线稿图标，无参数表。判据：无表格 + 无参数图

中广欧特斯 | 官网产品一览（产品线/型号清单） | https://www.outes.com/products.html ；https://nt.outes.com/ | HTTP 200，仅得到 46 个产品名+链接（境界/双子星Super/双子星E+/暖洋洋/旭日/云境等），无任何参数数值

中广欧特斯 | 内蒙古和林格尔盛乐镇采暖项目中的 ZGR-150IIAD 工况数值 | https://www.outes.com.cn/index.php/caseInfo/184.html | HTTP 500 Server Error（同一商用站故障）

中广欧特斯 | 商用站模块机/大多联/单元机分类页参数 | https://nt.outes.com/index.php?c=category&id=13,14,33,34 | HTTP 200，但 "模块机"（id=34）分类页为空（0 个产品链接）；其余三类的产品详情页（c=show&id=xx）未再展开——已从其他渠道取得同一产品线的完整工况数据，故停止

中广欧特斯 | 阿拉善盟煤改电中标明细中的性能参数 | https://www.ccgp-neimenggu.gov.cn/gpx-bid-file/tenant/152901/2024/7/6/402881e19062e6aa019081ee4fc871c6/gpx-bidconfirm/402881cc8f7aba8b01912285dba711c8.pdf?accessCode=98eb355bb0e130c6f77089a918fe52dd ；https://www.ccgp-neimenggu.gov.cn/gpx-bid-file/152921/gpx-bidconfirm/2022/8/23/402881df829679e00182ca883af4754a.pdf?accessCode=01b7bf0adaea34c9b364450b3a605928 | HTTP 200、PDF 有效（21 页 / 18 页），但内容仅为"中标（成交）明细"：只列 品目/型号/品牌/制造商/产地/数量/单价/单位总价（如 ZGR-14ⅠBDBPG6R、ZGR-16ⅠBDBPG6R、ZGR-25ⅡADBPG5、ZGR-28ⅡADBPG6R、ZGH-85ⅠAD、ZGH-170ⅡAD），**无制热量/输入功率/COP**。已存档为 sources/专业品牌/中广欧特斯_阿拉善*_煤改电中标明细.pdf，未用于取数

中广欧特斯 | CQC 认证证书中的制热量/COP | https://www.cqc.com.cn/www/upload/files/upload/resources/file/2022/03/08/41794.pdf ；https://www.cqc.com.cn/www/upload/files/upload/resources/file/2024/06/03/60810.pdf ；https://www.cqc.com.cn/www/upload/files/upload/resources/file/2024/04/23/60465.pdf | HTTP 200、PDF 有效（111 页 / 109 页），但证书表栏目只有：产品名称、制造商、获证规格型号、证书编号、生效日期、有效期、品牌；型号串里仅附带**额定出水温度**（41℃ 或 35℃ 或 55℃），**无制热量、输入功率、COP**。已存档 2022/2024 两份汇编于 sources/专业品牌/，未用于取数

中广欧特斯 | 聊城项目报价明细表（参考） | http://ggzyjy.liaocheng.gov.cn:8601/tpframe/rest/igetattach/getattach?AttachGuid=e11de0ad-292e-4bee-aa8f-af8e141a2546 | curl 退出码 000（连接超时/被拒，该端口 8601 不可达），未取得任何字节

中广欧特斯 | 赤焰·变频H系列 ZGH-220ⅡBDBP 的完整二维参数表（35/41/45/50℃×各环温） | https://www.outes.com/news/newsInfo/25504/10.html （官网新闻，已取得） | 拿到的是新闻体文本：仅 2 个制热量点（-12℃ 最大 141.8kW、-20℃ 最大 117.6kW）+ HSPF 2 值，**未公布输入功率、COP 与逐出水温度分档值**；该系列无独立产品页可查（官网产品列表里没有赤焰型号）

中广欧特斯 | 官方"选型手册/样本 PDF"下载 | 站内无下载栏（www.outes.com、nt.outes.com、www.zggroups.com 三站首页均无 .pdf 链接）；外部检索到的《中广欧特斯》样本仅存于付费文库 | 官网不提供样本 PDF；付费文库需登录/付费

中广欧特斯 | 双子星·Pro 系列参数表 | http://m.hvacr.cn/sells/2648352.html?cardid=1666422 | 慧聪网商品页，仅型号（ZGR-10ⅠADBPG3R/3HP）与图片，无逐工况参数；未取得可引用数值

====================================================================
二、生能（浙江正理生能科技有限公司 / Hien）
====================================================================

生能 | 官网产品页的逐工况参数表 | https://www.hien.cn/ ；https://www.hien.cn/business/74.html（商用冷暖）；https://www.hien.cn/business_detail/2500.html、2501、2502、2503、434、435、453、454、455、457 | 全部 HTTP 200；44 个 business_detail 产品页**逐一检查后 0 个 <table>**，正文无任何制热量/功率/COP 数字（标题仅"Dlrk-55ⅡBM/A2 V1.0"等型号）；判据：无表格 + 无参数文本。该站为纯形象站，产品参数不在网页上

生能 | 《生能空气能渠道冷暖配置选型》40 页选型手册（含名义制热量/IPLV(H)/水流量等栏目） | https://max.book118.com/html/2024/0410/7145131153006063.shtm ；预览接口 https://openapi.book118.com/getPreview.html?&project_id=1&aid=7145131153006063&t=0.123&view_token=<页面内token>&page=1 | 页面 HTTP 200（标题与页数 40 页可得），但预览接口返回 {"status":10002,"message":"非法操作"}（缺少正确的签名参数 t）；该文库为**付费/需登录**站点，不绕过。仅拿到目录级信息（型号命名规则 D=I 单相/II 三相、参数栏目名），未拿到任何数值

生能 | 《生能空气源方案样本.doc》 | https://www.renrendoc.com/paper/266397625.html | 付费文库，需登录/付费，未取数

生能 | "全直流变频户用冷暖手册.pdf"、"2_超低温商用冷暖系列.pdf"、"生能空气能样册" | https://www.huaruizhicheng.com/sqfw/chakan.aspx?leibie=45&fenlei=1 | 华瑞志诚售前资料系统，页面明示**需密码/授权访问**，未取数

生能 | AMA 生能冷暖系列商用超低温空气源热泵产品样册 PDF（型号含 DLRK-45ⅡBM/C2、DLRK-65Ⅱ/C4、DLRK-80Ⅱ/C4、DLRK-160Ⅱ/C6、DLRK-320Ⅱ/C4、DKFXRS-15Ⅱ 等） | http://hvacdoc.cn/search/?wd=%E8%B6%85%E4%BD%8E%E6%B8%A9 ；http://hvacdoc.cn/api/search?wd=... ；http://hvacdoc.cn/api/doc/search?keyword=... | 搜索页 HTTP 200 但为 Nuxt SPA（返回 HTML 仅 2991 字节，无结果内容）；两个推测的 API 端点均 404 Not Found。样册 PDF 未取得

生能 | 中标/合同文件中的性能参数 | https://sx2gov2open2doc.uos.sxzfcg.zcygov.cn/1035IC/c81c5cc5-0d2d-4ed9-a8a7-2a897d2bce69.pdf （五台县豆村养老中心合同，DLRK-160II/C6）；https://xincai.zfcg.henan.gov.cn/cmsweb81e27e/anyang/rootfiles/2026/01/20/8cbc03b1abbd49a2aaa7a3ae1966c625.pdf （安阳分项报价明细表，DLRK-160II/C6 / DKFXRS-160II/C2） | 均 HTTP 200、PDF 有效、中文可正常抽取（无乱码），但内容只有 货物名称/型号/数量/单价/合价（DLRK-160II/C6 单价 64000 元、DKFXRS-160II/C2 单价 66000 元）与本国产品声明函，**无制热量/输入功率/COP**。已存档于 sources/专业品牌/，未用于取数

生能 | 宁夏/贺兰/灵武等煤改电中标公告中的性能参数 | https://ggzyjy.fzggw.nx.gov.cn/dzjy/001001/001001002/001001002003/20250424/4eb1d8c3-837e-40f5-b870-c1f269d0826e.html （银川兴庆区月牙湖乡 2025 四标段，DLRK-16/C）；https://ggzyjy.fzggw.nx.gov.cn/.../20240802/c89a9efa-919c-4571-8fdb-9712357e8537.html （贺兰县 2024 第二批七标段）；https://wap.qianlima.com/zb/detail/20240413_414029352.html （灵武市 2024） | 公告正文只列 型号 + 单价/数量（如 DLRK-8IBM/C1 单价 10059、DLRK-13IBM/C1 单价 11000、DLRK-15IBM/C1 单价 11975），**无性能参数**；且部分页面为第三方转载站带脱敏遮挡。未用于取数

生能 | 生能国际站 R290 EcoForce / EcoForce Max 系列逐工况参数 | https://www.hien-ne.com/uploads/R290-Monoblock-Heat-Pump5.pdf （已下载，7.97MB） | PDF 有效但为通用产品彩页，**未含**逐环境温度的 制热量/输入功率/COP 表；已存档 sources/专业品牌/生能Hien_R290_Monoblock_HeatPump.pdf，未用于取数

生能 | 生能 DLRK 系列在 出水温度 35/45/50/55℃ × 环温 -30～7℃ 的完整二维表 | 各渠道均已尝试（官网、国际站、经销商产品页、政府采购、认证） | 仅国际站 VigorLife 给出 3 档出水温度（35/41/50）×2 档环温（-12/-20）；**未取得** 出水 45/55℃ 与环温 -30/-25/-15/-7/0/7℃ 的交叉点。DLRK-185II/C2 与 DLRK-20IBP/C1 只有 3 个离散工况点（名义1/名义2/制冷），DLRK-20IBP/C1 另有 1 条出水 41℃ 的五点变工况曲线

====================================================================
三、通用说明
====================================================================
- 未绕任何登录、付费墙、验证码。book118 / renrendoc / 华瑞志诚 三家付费或授权站点在"需付费/需登录/需密码"处即停止。
- 中广欧特斯商用站 www.outes.com.cn 在本次全程（18:40–19:10）持续返回 HTTP 500，非我方网络问题（首页与子页错误页字节数一致）。
- 政府站（ccgp.gov.cn、ccgp-neimenggu.gov.cn、gcycloud.cn、江苏/宁夏/山西/河南平台）在本机可直连；海外站（hien-ne.com / hbzhan.com 图片 CDN）需走代理 127.0.0.1:7897，且 hbzhan 图片必须带 Referer 才不 403。
```

### ## 附C. 阿尔普尔 AIRPOWER + 力诺瑞特 LERRI

```
品牌 | 想找什么 | 试过的 URL | 失败原因
阿尔普尔 | 官网产品页"技术参数"栏的制热量/输入功率二维表 | http://www.airpowercn.com/product/724.html 至 /738.html（12 个产品页全覆盖） | 页面第 3 个 tab「技术参数」div 恒为空（lenraw=30，无内容）；官网只发宣传文案不发参数表
阿尔普尔 | 同上（镜像站/英文站） | http://www.airpower.cn/product/709.html 至 /729.html（18 个产品页）；http://www.airpower.cn/case/97.html | 同样无任何 table/参数文本，均只有营销文案
阿尔普尔 | 产品注册/检验报告原件（报告号 2017LK1101，国家压缩机制冷设备质量监督检验中心） | http://jntzcjj.jinan.gov.cn/module/download/downfile.jsp?...filename=da7b4ac0cd3b4ab7823e080ea7b7d688.pdf | HTTP 403（该站 WAF 拒绝，带 Referer/cookie/完整浏览器头、走代理与直连均 403）；找不到该报告的其他镜像
阿尔普尔 | 中标公告里列出的机组实际技术参数表 | https://www.zhaobiaocn.com/info_2_299817.html（浑南敬老院 AZ-1850W/EHTFB）；http://ccgp.yingkou.net.cn/Html/NewsView.asp?ID=27870（营口大石桥 AZ-90W/ERPBD） | 公告只给品牌/型号/数量/单价，无制热量/输入功率/COP；营口站 HTTP 502 不可达
阿尔普尔 | 政府采购合同附件的"产品技术参数"表 | https://www.jdsbzb.com/view/105/UFN6G40BCNo2oqlzCzEl.html（营口盖州003包合同） | 该站只登合同正文摘要（合同编号/甲乙方/金额），技术参数附件未上网
阿尔普尔 | 同上（采购合同） | https://szj.hebei.gov.cn/hbggfwpt/...（海兴县2024，阿尔普尔中三、五标段） | 公示只到"中标单位+投标报价+质保期"，无产品参数附件
阿尔普尔 | 能效标识备案数据（制热量/输入功率/COP） | http://www.energylabel.gov.cn/ 、https://www.energylabel.gov.cn/ | DNS 解析失败（NXDOMAIN，本机 nslookup 与直连均解析不到域名），站点不可达；备案数据拿不到
阿尔普尔 | 产品样本/选型手册 PDF | WebSearch 多轮（"阿尔普尔 空气源热泵 样本 技术参数表 制热量 kW 输入功率 出水温度"） | 无公开可下载的官方样本 PDF；命中的均为付费文档站（renrendoc/book118）预览或非本品牌样本（AIRWELL/PARKAIR 等他牌）
阿尔普尔 | 经销商/电商产品页参数表 | https://product.11467.com/info/2873567.htm ；https://m.11467.com/product/d3117407.htm ；https://sdhs0001.gys.cn/supply/5338329538.html 等 | 11467 返回 HTTP 429 限流；gys.cn 返回 HTTP 520 + "请滑动验证码"（按规矩不绕验证码）；唯一放行的 AFR-275W 页只有"额定制热量 27.5W"（单位疑为笔误）无功率无 COP
阿尔普尔 | 官网产品图片中是否藏参数表 | http://www.airpowercn.com/data/upload/202505/1747041221150408.jpg 等 13 张 | 只有 1 张是参数表，且是"基站空调参数配置表"（AF-40WG/LADBA、AF-75WG/LADBA，DC48V 机房空调），不是采暖热泵；其余为示意图/案例照片
力诺瑞特 | 官网热泵品类的产品参数表 | http://www.linuo-paradigma.com/products_list/1247963957624983552.html（热泵户用采暖）等 6 个分类页 + 10 个 products_kongqi 页 | 中企动力(300.cn) SaaS 站，正文由 JS 动态渲染；服务端 HTML 里只有页眉页脚，抓不到参数表
力诺瑞特 | 官网"登峰系列"（LPR-18ID/CER01）参数表 | http://www.linuo-paradigma.com/product_detail/1247964001623232512.html | 该页唯一的 table 为空；68 张图里没有参数表图；页面无制热量/输入功率文本
力诺瑞特 | 说明书下载栏里的热泵参数 | http://www.linuo-paradigma.com/companyfile/1/ 、/companyfile/34.html | 下载栏只有电热水器/太阳能热水器说明书和碳足迹报告，无热泵参数表
力诺瑞特 | 能效标识备案数据 | 同 energylabel.gov.cn（DNS 失败） | 站点不可达
力诺瑞特 | 产品样本 PPT/PDF 原件 | https://www.renrendoc.com/paper/237674732.html（力诺瑞特空气源热泵产品介绍-20230115.ppt） | 付费文档站，需登录/积分下载（按规矩不绕付费墙）
力诺瑞特 | 中标公告里的机组实际技术参数表 | https://ggzy.qingdao.gov.cn/TradeDetals-ZtbShow/120648-...（莱西沽河2022第五标段） | 公告"主要标的中标信息表"只有品牌/产地/规格型号/数量/单价，无制热量/输入功率/COP
力诺瑞特 | 同上 | 北京市怀柔长哨营2025 长哨营中标公告的 PDF 附件 | ccgp-beijing.gov.cn 上该项目的多个 P0202...pdf 附件路径全部 HTTP 404（文件已下线）
力诺瑞特 | 采购合同附件的产品技术参数表 | 中国政府采购网 ccgp.gov.cn 站内检索 | 返回"频繁访问!中国政府采购网"限流页，无法检索
力诺瑞特 | 山东省级监督抽查的实测数值 | http://amr.shandong.gov.cn/art/2026/8/26/art_76502_10317152.html 附件1/附件2 | 附件只有"合格/不合格"结论与企业-型号清单，未公布名义制热量/消耗功率/COP 的实测值（该批"抽查未发现不合格项目"，故无不合格明细表）
两家通用 | 2023-2025 各年山东省级监督抽查含实测值的版本 | amr.shandong.gov.cn 站内 | 抽查汇总表格式只给结论，不给数值；无法从抽查渠道取得工况点数据
两家通用 | 一招投标附件形式的"投标产品技术参数表" | 多轮 WebSearch（含 site:ccgp-hebei.gov.cn 限定） | 未检索到任何同时含"阿尔普尔/力诺瑞特"与"名义制热量/名义制热消耗功率"具体数值的公开附件
```


---

## 八、归档时剔除的件（已下载但无数据价值）

| 件 | 大小 | 剔除原因 |
|---|---|---|
| 生能 `https://www.hien-ne.com/uploads/Specifications.pdf` | 134 MB | 下载成功（有效 PDF，2 页），但正文仅 "What are DC Inverter Air to Water Heat Pumps?" 之类宣传文字，**全文无 COP、无任何小数数值**，且不被 CSV 引用 → 剔除，避免占据 134 MB 归档体积 |
| `CRAA_产品性能认证名录_2022.pdf` | 305 KB | 与 `CRAA_产品认证名录_含广东海悟_202211.pdf` **sha256 完全相同**（`4168d93492877027…`），重复件 → 保留后者 |
| `招标门槛_巴彦淖尔_低环境温度空气源热泵_分档.pdf` | 0 B | 空文件（源站 502），删除 |
| `招标门槛_青岛_热泵热水机_41℃50℃_2包采购需求.pdf`、`qd.pdf`、`bj.docx` | 508 B / 552 B | 实际是 HTML 反爬页/404 页被存成 .pdf/.docx，删除 |
| `阿尔普尔_天津滨海新区2018煤改电_中标产品.pdf` | 64 KB | 与 `阿尔普尔+力诺瑞特_AF-140W-ERPBA与LPHS-15.5IFD_天津滨海新区2018煤改电中标品牌型号表.pdf` sha256 相同，重复件 → 保留后者 |
| 河南烟草许昌电能烤房采购 PDF（145 页） | — | 开箱检查后全文无海悟技术参数（项目为电能烤房非采暖热泵），已由分派代理删除，未入库 |
