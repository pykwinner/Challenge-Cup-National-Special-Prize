# 没拿到清单（not_found）

逐条格式：`想找什么 | 试过的 URL | 失败原因`
抓取环境：Windows 11 + Git Bash；海外站点走本地代理 `http://127.0.0.1:7897`；UA 统一为
`Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36`。

---

## A. `https://www.energylabel.gov.cn`（中国能效标识网旧域名）——**确认不通**

**结论：该域名在 DNS 层面已不存在（NXDOMAIN），不是超时、不是防火墙拦截、不是 502。**
因此「用 IP 直连」这一条不成立——没有可解析的 IP。

### A1. DNS 解析（四个解析器一致返回 Non-existent domain）

| 查询 | 解析器 | 结果 |
|---|---|---|
| `www.energylabel.gov.cn` | 本地（路由器 `fe80::1`） | `Non-existent domain` |
| `energylabel.gov.cn` | 本地（路由器 `fe80::1`） | `Non-existent domain` |
| `www.energylabel.gov.cn` | `223.5.5.5`（阿里 DNS） | `Non-existent domain` |
| `energylabel.gov.cn` | `223.5.5.5` | `Non-existent domain` |
| `www.energylabel.gov.cn` | `119.29.29.29`（DNSPod） | `Non-existent domain` |
| `energylabel.gov.cn` | `119.29.29.29` | `Non-existent domain` |
| `www.energylabel.gov.cn` | `8.8.8.8`（Google） | 先 `DNS request timed out`，重试后 `Non-existent domain` |
| `energylabel.gov.cn` | `8.8.8.8` | 同上 |

### A2. HTTP/HTTPS 请求（`http_code | time_total | remote_ip`）

| URL | 结果 |
|---|---|
| `https://www.energylabel.gov.cn/` | `000`，0.0035 s（DNS 失败） |
| `http://www.energylabel.gov.cn/` | `502`，0.0020 s（由本地代理 127.0.0.1 直接返回，未出网） |
| `https://energylabel.gov.cn/` | `000`，0.0037 s |
| `http://energylabel.gov.cn/` | `502`，0.0020 s |
| `https://www.energylabel.gov.cn/search` | `000`，0.0039 s |
| `http://www.energylabel.gov.cn/search` | `502`，0.0023 s |
| `https://energylabel.miit.gov.cn/`（工信部子域猜测） | `000`，0.139 s |

`curl -v` 细节：代理隧道本身可以建立（`CONNECT ... HTTP/1.1` → `HTTP/1.1 200 Connection established`），
随后卡在域名解析，说明失败点不在网络链路而在域名本身。

### A3. 域名注册状态（RDAP）

| URL | 结果 |
|---|---|
| `https://rdap.zdns.cn/domain/energylabel.gov.cn` | HTTP `404` / `{"errorCode":"404","title":"Object not found"}` |

→ 该域名在 CNNIC `.gov.cn` 注册库中查无此对象。

### A4. 通过搜索引擎核实是否有新域名（结果：**旧域名已废弃，官网已迁移**）

- 检索 `中国能效标识网 备案信息查询 官网 网址 energylabel`
- 关键旁证：中国标准化研究院（CNIS）《关于能效标识备案信息系统优化升级的通知》
  `https://www.cnis.ac.cn/wap/dh/tzgg/202501/t20250103_59327.html` —— 现行查询入口为
  **`https://www.energylabel.com.cn`**（「产品备案查询」/「实验室备案查询」）。
- 其他官方文件同样引用 `www.energylabel.com.cn`；早期文件（约 2020 年前后）曾用过
  `www.energylabelrecord.com`；2012 年新闻报道中出现过 `www.energylabel.gov.cn`。
- **实测确认**：`https://www.energylabel.com.cn/` → HTTP `200`，标题 `中国能效标识网`，
  DNS 解析到 `219.142.42.221`。

### A5. 其余域名变体（一并测过，均不通）

| URL | 结果 |
|---|---|
| `https://www.energylabel.org.cn/` | `000`，0.125 s；`nslookup www.energylabel.org.cn` → `Non-existent domain` |
| `http://www.energylabel.org.cn/` | `502`（本地代理） |
| `https://energylabel.org.cn/` | `000`，0.117 s |
| `https://www.energylabel.cn/` | `000`，5.24 s（超时类） |
| `https://www.energylabel.com.cn/` | **`200` ✓ 可用（现行官网）** |

### A6. 官方站可用后的实际收获（替代旧域名，已完成）

- 站点为 Vue SPA，数据经 `/admin-api/gateway/productRegistration/*` 提供。
- 已抓到 **640 条备案记录**（2024 版 300 / 2019 版 40 / 热泵热水机 300），字段含
  `名义制热量`、`名义制热消耗功率`、`COPh`、`COPdh`、`APF`、`HSPF`、`能效等级`、`额定出水温度(℃)`。
- 原始响应：`energylabel_能效备案_明细.jsonl`、`energylabel_能效备案_列表.jsonl`、
  `energylabel_列表原始响应/`（列表页原始 JSON）。
- **限制**：备案记录**不包含**制热工况的源侧空气温度/出水温度数值（2024 版仅给「名义制热工况 / 低温制热工况」
  的标签，不给温度）。故 CSV 中 `源侧温度_C`、`出水温度_C` 对 2024 版备案行留空，**未按标准条文回填**。

---

## B. 第三方检测报告（机构官网直发版）——**not_found**

| 想找什么 | 试过的 URL / 途径 | 失败原因 |
|---|---|---|
| 国家压缩机制冷设备质量监督检验中心 对外公开的「检测报告库/报告查询」页面 | 由合肥通用机电产品检测院（同址挂牌）官网及搜索引擎入口检索 | 未找到任何机构官网直接公开可下载的检验报告 PDF；报告按委托方交付，不对外发布 |
| 合肥通用机电产品检测院 空气源热泵检测报告 PDF | `合肥通用机电产品检测院 空气源热泵 检测报告 pdf 制热量 输入功率` 检索；落地到政采附件 | 只找到**夹带在投标文件里**的报告扫描件（见下），无独立公开版本 |
| 中国建筑科学研究院建筑环境与能源研究院 检测报告公开 PDF | `中国建筑科学研究院 建筑环境与能源研究院 检测报告 空气源热泵 供热 公开 pdf` | 仅找到技术报告/期刊 PDF（如 `https://www.car.org.cn/ueditor/php/upload/file/20180606/1528250942987183.pdf`，属**技术发展报告**，非检测报告）；期刊全文在知网，属付费墙，按要求不绕 |
| 国家空调设备质量监督检验中心 检测报告 | 同上，随建筑环境与能源研究院一并检索 | 未找到公开检测报告 PDF |
| 中国质量认证中心（CQC）「低环境温度空气源热泵（冷水）机组」节能认证公告中的 制热量/输入功率 明细 | `https://www.cqc.com.cn/www/upload/files/upload/resources/file/2022/04/18/41925.pdf`（97 页，200 OK）<br>`https://www.cqc.com.cn/www/upload/file/2026-08/1786950788270.pdf`（96 页，200 OK） | 两份文件确为 CQC 节能认证公告且含「低环境温度空气源热泵（冷水）机组」条目（含品牌/制造商/获证型号/证书编号/能效等级），但**公告条目不含制热量、输入功率、COP 数值**；已下载核对后未用于 CSV |
| 全国认证认可信息公共服务平台「检验检测报告编号查询」反查报告实测值 | 未接入 | 需交互式查询（验证码类），按要求不绕验证码 |

### B-1. 已下载但**未用于数值**的报告扫描件（如实记录）

| 文件 | 机构 / 报告号 | 为什么没用 |
|---|---|---|
| `广东政采附件_投标文件（含合肥通用2020LK1398检验报告扫描）.pdf` | 合肥通用机电产品检测院有限公司／国家压缩机制冷设备质量监督检验中心，`No:2020LK1398`，2020-07-28，共 6 页 | 逐页读图核对（第 60 页等）：**受检对象是两管制交流电机空调机组**，不是低环境温度空气源热泵；其制热相关数据不作为本 CSV 的 ASHP 依据 |
| `滨州公共资源交易附件（含合肥通用检验报告扫描）.pdf` | 合肥通用（搜索索引称 `No 2020LK1763`） | 文件为整份扫描件，文字层极薄，未能稳定提取含工况的制热数据；**未取任何数值** |
| `福建政采附件_投标文件（含中家院WK-24-0213检验报告扫描）.pdf` | 中家院（北京）检测认证有限公司／国家家用电器质量监督检验中心，`WK-24-0213` | **已成功取数**——见 CSV 中 6 行 `数据性质=检测报告`（属本任务唯一取得完整实测数据的第三方检测报告，但机组类型为「空气源热泵热水机」，不是「低环境温度空气源热泵（冷水）机组」） |

### B-2. 未能下载到的报告类线索（链接已失效）

| 想找什么 | 试过的 URL | 失败原因 |
|---|---|---|
| 北京某煤改电项目招标文件（含检测报告评分办法） | `http://www.ccgp-beijing.gov.cn/xxgg/qjzfcggg/qjzbjggg/P020191101689300057800.pdf` | `404`（返回 552 字节 HTML 404 页，已删除） |
| 北京某煤改电项目招标文件（另一份） | `http://www.ccgp-beijing.gov.cn/xxgg/qjzfcggg/qjzbjggg/P020190918537629989290.pdf` | `404` HTML |
| 房山区2022煤改电 第二包招标公告（docx） | `http://www.ccgp-beijing.gov.cn/xxgg/qjzfcggg/qjzbgg/P020221017435987405236.docx` | `404` HTML |
| 北京「15个村空气源热泵供应企业招标项目」 | `http://www.ccgp-beijing.gov.cn/xxgg/qjzfcggg/qjzbjggg/P020170829007889140170.pdf` | `404` HTML |
| 北京市「垃圾转运站空气源热泵主机和散热器项目」（含散热器报价线索） | `http://www.ccgp-beijing.gov.cn/xxgg/qjzfcggg/qjzbjggg/P020180807408554833883.pdf` | `404` HTML |
| 佛山市技术要求文件（另一份，含低温制热量条文） | `http://fszj.foshan.gov.cn/attachment/0/616/616668/7171002.pdf` | `404` HTML（另一下载成功：`0/271/271948/5313030.pdf`） |
| 内蒙古巴彦淖尔 供热面积档位技术参数表 | `http://ggzyjy.bynr.gov.cn/EpointWebBuilder/WebbuilderMIS/attach/downloadZtbAttach.jspx?attachGuid=daafbccb-8efd-4697-b053-1138538beb66&appUrlFlag=ztb001&siteGuid=7eb5f7f1-9041-43ad-8e13-8fcb82ea831a` | `502`，0 字节（直连与代理均试） |
| 宁夏灵武市2024农村清洁取暖项目 磋商文件 | `https://222.75.70.90:7075/NXGPPSP_MG/downloadFileServlet?num=8b80b23e8e132df0018e1940e325677c&flag=isWebsite` | `502`，0 字节 |
| 山东省市场监管局 检验项目文件 | `http://amr.shandong.gov.cn/module/download/downfile.jsp?classid=0&filename=e4b8d63fed0b43d9a554bd88ed5b040b.pdf` | 返回 `118` 字节 HTML，非 PDF，已删除 |
| 某厂商热泵产品样本 PDF | `https://ty-boiler.com/tyboiler/pdf/热泵产品tyboiler.cn.pdf`（搜索结果中的 URL 编码形式） | `404`；站点 `/tyboiler/pdf/` 目录亦 `404` |
| 环保在线产品页 `DLRK-185II/C2` 的名义1/名义2 制热量与输入功率 | `https://shop.hbzhan.com/sale/detail-17362793.html` | 页面仅含工况**定义**文字（7/6℃、45℃；-12/-14℃、41℃），**具体数值表不在 HTML 文本中**（疑为图片/异步加载）。检索摘要中出现过的数字无法在页面原文中逐字核到，**故一律未采用** |
| 中国建筑科学研究院建筑环境与能源研究院 正式检测报告全文 | 官网 / 期刊 | 仅摘要页；全文在知网（付费墙），按要求不绕 |

---

## C. 部分能效备案字段缺口（非未找到，但需标注）

| 想找什么 | 结果 |
|---|---|
| 2024 版（GB 19577-2024）备案记录的**低温制热量、低温制热消耗功率**实测值 | 备案公告**只给低温 COP（COPdh），不给低温制热量/功率**。CSV 中 2024 版「低温制热工况」行只有 COP 一列有值，制热量/输入功率留空 |
| 2024 版备案记录的**名义/低温工况的源侧与出水温度数值** | 备案公告不载温度数值，CSV 对应列留空（**未按标准条文回填**） |
| 2019 版（GB 37480-2019）备案记录，全量 | 接口仅返回 **40 条**（`total=40`），已全部取得 |
| 2024 版 / 热泵热水机 备案记录，全量 | 接口分页每类可返回约 500 条（`total=500` 为接口上限显示值），本次每类取 **300 条**（前 6 页×50） |
