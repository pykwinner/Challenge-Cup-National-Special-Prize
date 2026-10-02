# 盾安 / 清华同方 采集失败清单

采集时间：2026-10-02（Asia/Shanghai）。
环境：`https_proxy=http://127.0.0.1:7897`；UA=Chrome 120。
所有条目均为**尝试过且未取得可用性能数据**的入口。凡涉及付费或需授权的，一律记录、未做任何绕过。

---

## 一、官网类（内容为空 / 域名不可达）

| # | URL | 失败原因 |
|---|-----|---------|
| 1 | `https://www.dunanac.com/` | TLS 证书**已过期**（`schannel: SEC_E_CERT_EXPIRED`）。用 `-k` 可访问，返回 200。 |
| 2 | `https://www.dunanac.com/product_info.aspx?id=*`（共 25 个型号页：安悦 i-Star、安炫 i-Shine、安欣 Mate、安杰 i-Super、安润 i-Dream 水地源等） | 页面 200，但**「特点」/「规格」两个 tab 的容器在服务端返回的 HTML 里是空的**（`<div class="container"></div>`），全部规格字段为零。官网不发布制热量/输入功率/COP 参数表。 |
| 3 | `https://www.dunanac.com/designer.aspx` → `designer_info.aspx?id=486/487`（煤改电专用低环温空气源热泵、强热型空气源高温热水机组） | 200，但**无任何附件链接**（无 .pdf/.dwg/.doc）。 |
| 4 | `https://www.dunanac.com/plan.aspx` | 行业解决方案，纯文字，无参数。 |
| 5 | `http://p.dunanac.com/articleInfo.html?id=c0cf5984-...` (安炫 i-Shine) | 200，但只有宣传性文字（基础型号 69/75/103/110/150/170、制热量 69–2720 kW），**无工况点**。 |
| 6 | `http://p.dunanac.com/articleList.html?...` | HTTP 404（`/Temp/weixin/articleList.aspx` 不存在）。 |
| 7 | `http://m.dunanac.com/` | 200，返回 715 字节，空壳。 |
| 8 | `http://en.dunanac.com/` | 200，英文站与中文站同结构，同样无规格数据。 |
| 9 | `http://www.dunan.net/`（盾安环境，母公司） | 200，但只有制冷配件/热工科技/汽车热管理业务介绍，**不列热泵机组参数**。 |
| 10 | `http://www.thrh.com.cn/`（清华同方人工环境 官网） | 代理下 502 Bad Gateway；直连 code=000（DNS/连接失败）。**该域名已不可用**。 |
| 11 | `https://www.thrh.com.cn/` | 同上，code=000。 |
| 12 | `http://www.thrh.com/` | HTTP 404。 |
| 13 | `http://www.wxthrf.com/`（无锡同方人工环境 猜测域名） | HTTP 502。 |
| 14 | `http://www.wxthrh.com/` | HTTP 412 Precondition Failed。 |
| 15 | `http://www.tongfang-rh.com/` | HTTP 502。 |
| 16 | `http://www.tongfang.com.cn/` | HTTP 502。 |
| 17 | `http://www.tsinghua-tongfang.com/` | HTTP 502。 |

## 二、付费墙类（已记录，未绕过）

| # | URL | 说明 |
|---|-----|------|
| 18 | `https://jiadianshuo.com/jdskongtiao/jdsdunan/795660790491652`（【盾安】产品手册，46 页 / 63.7 MB） | **支付宝扫码支付 1 元**才能下载。**PAYWALL —— 未尝试绕过。** 搜索引擎快照显示其含风冷螺杆名义工况表（FLRM210C 制冷210/制热279 kW；FLRM470C 470/449；FLRM744C 744/790），但**无法从原始文件核验，故未计入 CSV**。 |
| 19 | `https://jiadianshuo.com/jdskongtiao/jdsdunan/972262460651441`（盾安 FL(R)M 风冷螺杆技术手册 2011 版，约 10 页 / 19.7 MB） | 同上，**付费 1 元。未绕过。** 快照称含 R22 与 R407C 两套「按环境温度、出水温度折算的变工况修正系数表」——这正是 2-D 数据所缺的部分，但**未能取得原件**。 |
| 20 | `https://jiadianshuo.com/jdskongtiao/jdsdunan/540276601929285`（【盾安】风冷涡旋，FWRM 系列技术手册 2012 版，18 页 / 25.8 MB） | 同上，**付费 1 元。未绕过。** 快照称含 FWRM015F/020F/030F/040F 基础模块简表（单机制冷 51/69/103/138 kW、单机制热 58/78/117/157 kW）及 **-20~20℃ 制热变工况修正表**，但**输入功率与 COP 未在快照中出现，无法核验，故未计入 CSV**。 |
| 21 | `https://www.youzhicai.com/nd/80b3a605-d401-4375-9f78-24027d0fdc59-1.html`（安徽两淮建设 南京区域空气源热泵采购，品牌要求含**盾安**） | 招标文件需**平台注册 + 付费 200 元**下载。**未绕过。** 公告正文不含技术参数。 |
| 22 | `http://www.huaruizhicheng.com/sqfw/chakan.aspx?leibie=1`（华瑞志诚 产品售前资料服务管理系统） | 列出「清华同方空气源热泵样本（2017-07-09）」「空气源热泵热水器产品彩页样本PDF（2017年版）」「清华同方空气源热泵整体机组使用说明书（2018-01-29）」等条目，但页面要求**输入密码 / 实名注册并经公司授权**（「本程序需要输入密码才可以使用」）。**属访问控制，未绕过。** |

## 三、站点失效 / 内容不可提取

| # | URL | 失败原因 |
|---|-----|---------|
| 23 | `http://www.tfqkkj.com/pd.jsp?id=23`、`?id=24`、`?id=25` 及站内其它页（FS-L-R 模块机、FS-Z 空气源热泵、FS-L 能源之星） | 全站只返回 73 字节的 `<script src="/flyvpn.js">` —— **域名已被劫持/停用**。搜索引擎索引里该站的 FS-L/FS-Z 参数（如 FS-Z-R 250I 制热量 313 kW、能效比 3.1）**无法从活页面核验，未计入**。 |
| 24 | `http://hvacdoc.cn/document/f5bef53fd39ce7a3`（《TONGFANG同方热泵采暖机组》产品样册 44 页 / 31.12 MB，含 HSWR/HSLR/HSYR/HSNR 系列） | 页面为 Nuxt SSR 空壳，服务端只返回 2,924 字节，HTML 内**没有任何下载直链**。`https://hvacdoc.cn/search/?wd=680&page=1&size=10&type=0`（SGHP 水源热泵样册 31 页 / 22.71 MB）同样如此。**未取得文件。** |
| 25 | `https://www.emerson.cn/documents/commercial-residential/case-study-copeland-scroll-variable-speed-zww-for-heating-zh-cn-5480320.pdf`（艾默生 北京密云煤改电实测案例） | **HTTP 404（文档已下架）**，返回 5,185 字节 HTML 而非 PDF。搜索引擎摘要显示该文含 **清华同方 HSYR-DG-18(D)BPIII** 名义制热（环境 -12℃、出水 41℃）制热量 14.4 kW、COP 2.34、IPLV(H) 2.82 —— **但原文取不到，故未计入 CSV**。 |
| 26 | `https://www.cqc.com.cn/dynamic/contentcore/resource/download?ID=35843` | 返回 196 字节 HTML（非 PDF），下载接口失效。 |
| 27 | `https://max.book118.com/html/2020/1106/8135013073003013.shtm`（盾安风冷涡旋式冷水热泵机组说明书.docx） | 200，但正文是**JS 门控的付费预览**，静态 HTML 里没有参数表内容。同理 `max.book118.com/html/2018/1226/8004063074001141.shtm`、`renrendoc.com/paper/430171941.html`、`renrendoc.com/paper/430463154.html`、`wenku.so.com/d/6d3543c8ff4f90630c719858bd05cb2d`。 |
| 28 | `https://www.energylabel.gov.cn/`（中国能效标识网） | 走代理 code=000，直连 502。**能效备案核查这条路在本网络下不通**，未能取得备案 COP。 |
| 29 | `http://dunan.ces.cn/sell/itemid-6489.html`（YHAC40CE010 风冷式冷水/空气源热泵）、`http://dunan.ces.cn/sell/itemid-6476.html`（YCAE 系列模块机） | 站点标题挂着「北京盾安空调设备安装有限公司」，但页面**品牌字段写的是「约克」** —— 是约克 YHAC/YCAE 产品，**不是盾安机组，不能作为盾安数据**。 |
| 30 | `http://www.hbzhan.com/chanpin/26574008.html`（安炫i-Shine）、`26574242.html`（安润i-Dream 水地源）、`26574317.html`（安杰i-Super） | 200，但内容是盾安官网产品简介的转载（只有「型号 / 制冷量范围 / 制热量范围 / 运行范围」），**无逐型号工况点**。 |
| 31 | `https://www.zhilengxuebao.com/...` 论文内页 | 论文正文（4 页）已取得并入库；但**文中只有 FLRM360 的名义点（359 kW / 108.8 kW）**，无 -20/-12℃ 多工况表。 |

## 四、取到但不够用的政府/招标文件（附说明）

| # | URL | 情况 |
|---|-----|------|
| 32 | `https://ggzy.guizhou.gov.cn/hallweb/hall/attach/nosession/download?attachId=2ffcffe5-0ce9-4643-982a-5b7274064944` | 取得 75 页 PDF（贵州某空气能热水系统采购）。内含 GB/T 21362-2023 各工况**门槛值**（名义 7.0/6.0℃ 进出水 9/55℃：≥16.0 kW / ≤4.4 kW / COP≥3.7；低温 -7.0/-8.0℃：≥11.0 kW / ≤4.2 kW / COP≥2.7；融霜 2.0/1.0℃：≥14.4 kW / ≤4.3 kW / COP≥3.4；常温 20.0/15.0℃：≥41.0 kW / ≤8.9 kW / COP≥4.6）。**但全文未点名牌号，既非盾安也非清华同方，且为热水机而非采暖冷水机组 —— 故未计入 CSV。** |
| 33 | `http://www.lnsggzyjy.cn/...1166100c-66a0-4c3a-89f4-57ac0f01bc74.html` | 取得并入库（盾安 FWRMDC030F）。**只有单个名义点，无 -12/-20℃ 分档。** |
| 34 | `https://ggzy.qingdao.gov.cn/PortalQDManage/PortalQD/GetZbDownLoad?id=83243&filePOrB=2` | 取得并入库（64 页招标文件）。技术标准章给出超低温空气源热泵门槛（名义制热 ≥180 kW / COP≥3.45；低温名义制热 ≥120 kW / COP≥2.35；IPLV(H)≥3.0），并**明确定义**名义制热=45℃出水/7℃DB-6℃WB，低温名义制热=41℃出水/-12℃DB-14℃WB。**非品牌专属，故未写入品牌行。** |

---

## 四·补、更正：本地 CQC 2025-12 名录的文本层并没有坏

任务书提示 `sources/大品牌/CQC_低环境温度空气源热泵名录_2025-12.pdf` 的**中文文本层已损坏、需要 OCR**。
实测**并非如此**：用 PyMuPDF `get_text()` 可直接读出完整中文条目，无需 OCR。已直接抽取：

- **第 40 页**（清华同方／无锡同方人工环境有限公司，品牌「清华同方」）
  - `HSWR-D-09(D)BPGIIIJ R410A 地板采暖型` — 证书 CQC25701495119，2025-12-17 至 2030-12-16
  - `HSWR-D-18(D)BPGIIIJ R410A 地板采暖型` — 证书 CQC25701495118，2025-12-17 至 2030-12-16
  - `HSWR-D-20(S)BPGIIIJ R410A 地板采暖型` — 证书 CQC25701495120，2025-12-17 至 2030-12-16
  - `HSWR-D-09(D)BPGIII R410A 风机盘管型` — 证书 CQC25701495285，2025-12-18 至 2030-12-17
- **第 41 页**（浙江盾安机电科技有限公司，品牌「盾安」）
  - `FWRMDE150VG R32（风机盘管型）` — 证书 CQC25701493584，2025-12-19 至 2030-12-18
  - `FWRMDE050VG R32（风机盘管型）` — 证书 CQC25701493582，2025-12-19 至 2030-12-18

以上 6 条仅**型号佐证**，名录本身不含制热量/输入功率/COP，已按此性质写入 CSV。
名录的源头下载地址（未在本次重新下载，文件为工作区既有）：`https://www.cqc.com.cn/www/upload/file/2025-12/1766479076697.pdf`

## 五、结论性说明（供后续参考）

1. **盾安官网（dunanac.com）不发布任何热泵性能参数表** —— 25 个在售型号的详情页「规格」标签全部为空容器，设计师专区也无附件。盾安的 2-D 工况数据只能通过经销商/论文/招标三条外部途径获得。
2. **清华同方官网 thrh.com.cn 已下线**（DNS 不通 / 502），现存最完整的公开参数来源是经销商**北京华瑞志诚**的产品页（huaruizhicheng.com.cn），本次 23 个工况点中的 23 个来自该站。
3. 本次**未取得**任何牌子在 **-20℃ / -25℃ / -30℃ 分档**下的逐点制热量-输入功率-COP 二维表；未取得**水/地源热泵**（源侧水温 × 出水温度）的任何厂家参数表。
4. 唯一出现的 -12℃ 分档二维表来自清华同方 HSYR-DG-10C(D)E / HSYR-DG-15C(D)E / FS-L-H-70（7℃ 与 -12℃ 两行 × 35℃/45℃/60℃ 出水）。
