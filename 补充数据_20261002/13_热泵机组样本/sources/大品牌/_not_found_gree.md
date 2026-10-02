# 格力（GREE）低环境温度空气源热泵 —— 采集失败 / 未取到清单

采集时间：2026-10-02（Asia/Shanghai）
采集人：GREE 数据采集子任务
代理：`https_proxy=http://127.0.0.1:7897`（国内站直连，海外站走代理）
UA：`Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36`

---

## 一、格力官方站点（全部无法取到样本/参数）

| URL | 结果 | 失败原因 |
|---|---|---|
| `https://www.gree.com/` | HTTP 200，158 KB | Next.js 单页应用，HTML 内**零产品/下载链接**；正文由 `self.__next_f.push` RSC 流注入，抓到的全是 chunk 名与 antd 语言包 |
| `https://www.gree.com.cn/` | HTTP 200，157 KB | 与 `www.gree.com` 同一套 SPA；同为 JS-only |
| `http://www.gree.com.cn/` | HTTP 200 | 同上（HTTP 可用、HTTPS 走代理返回 000） |
| `https://www.gree.com/product` | **HTTP 404** | 路径不存在 |
| `https://www.gree.com/download` | **HTTP 404** | 无下载中心 |
| `https://www.gree.com/service` | **HTTP 404** | 无服务/资料页 |
| `http://www.gree.com.cn/product/`、`/download/`、`/service/` | **HTTP 404** | 同上 |
| `https://www.gree.com/cmsProduct/view/7cfe0930d774493da04ecc3324170e9b` | **HTTP 404** | 搜索结果索引到的旧官网产品页，CMS 记录已删除 |
| `https://www.gree.com/sitemap.xml` | **HTTP 404** | 无 sitemap |
| `https://www.gree.com/robots.txt` | **HTTP 404** | 无 robots（返回的是 SPA 外壳） |
| `https://www.gree.com/sitemap_index.xml` | **HTTP 404** | — |
| `https://global.gree.com/` | HTTP 200，115 KB | 海外官网首页，营销页，**无任何 PDF 文件链接** |
| `https://global.gree.com/upload/files/` | **HTTP 404** | 目录列举被关闭（该目录下确有文件，但无索引） |
| `http://gts.gree.com` | 未尝试登录 | 格力全球技术服务系统 GTS，**需账号登录**才能下载电子样本，按“不绕登录”规则放弃 |

> 结论：格力官网当前为 Next.js 纯前端渲染且**不对外提供样本 PDF 下载**（资料下载需走 GTS 登录或找经销商）。

---

## 二、经销商网站（有表但下载链路是死的）

| URL | 结果 | 失败原因 |
|---|---|---|
| `http://bjdcjz.com.cn/index.php/Product/view/id/2969.html` | HTTP 200，31 KB | “电子样本：点击下载”的 `<a href="" id="downName">` **href 为空**；`<span id="downName2">` 服务端未填充，页内 JS 反而把它清空（`$("#downName2").text("")`）⟹ 拿不到样本文件 URL；服务端 HTML 里也**没有**参数表 |
| `http://bjdcjz.com.cn/mobile.php/Product/view/id/2969.html` | HTTP 200，9 KB | 移动版更简，产品型号/能效等级字段为空，无表 |
| `http://www.bjdcjz.com.cn/index.php/Product/view/id/297.html`（模块机系列） | HTTP 200，27 KB | 页面正文无任何参数表，产品型号字段为空 |
| `http://www.hb-gree.com/cont/361.html`（LM 螺杆机） | HTTP 200，20 KB | 正文写着“产品性能参数表”，但**整张样本被切成约 50 张 PNG**（`/upload/20210601/*.png`）。该主机对批量取图做了限流：49/50 张返回 curl code 000 / 0 字节，只成功 1 张（且非参数表）。已读到的 4 张图均为营销页（瘦高齿设计/模块化运行/电子膨胀阀/伴热带） |
| `https://www.shfuyu.net/html/zhongyangkongtiao/gree/lgj/87.html`、`http://www.021kt.com/html/products/.../87.html` | HTTP 200 | 同为 LM 螺杆机页，可取的 9 张图逐张读图核对，**全部是营销插图**，未含性能表 |

---

## 三、政府 / 认证站点

| URL | 结果 | 失败原因 |
|---|---|---|
| `https://www.cqc.com.cn/dynamic/contentcore/resource/download?ID=62428` | **HTTP 404** | 搜索引擎索引到的新版名录深链已失效 |
| `https://www.cqc.com.cn/dynamic/contentcore/resource/download?ID=41198` | **HTTP 404** | 同上 |
| `https://www.cqc.com.cn/dynamic/contentcore/resource/download?ID=36704` | **HTTP 404** | 同上 |
| `https://www.cqc.com.cn/www/upload/files/.../2020/10/26/38729.pdf` | ✅ **HTTP 200** | 成功（直链 + Referer 可下载，见 receipts） |
| `https://www.cqc.com.cn/www/upload/files/.../2022/08/09/42278.pdf` | ✅ **HTTP 200** | 成功 |
| `https://www.cqc.com.cn/www/upload/files/.../2022/04/25/41938.pdf` | HTTP 200 | 可下载，但 sha256 与本地既有 `CQC_低环境温度空气源热泵名录_2022.pdf` **完全同源同字节**（3,136,585 B），故未重复入库 |
| `https://www.energylabel.gov.cn/`（中国能效标识网） | **HTTP 000（代理）/ HTTP 502（直连）** | 站点整体不可达，多次重试均失败；无法检索能效备案明细 |
| `https://sgj.xinjiang.gov.cn/sgj/jscp1/202212/.../0a5d240c5c7b4ec59c05ac1b93c8dc5d.pdf` | **HTTP 404** | 新疆节能产品目录 PDF 链接失效 |
| `https://gdgpo.czt.gd.gov.cn/gpx-bid-file/441701/.../fxbj....pdf` | HTTP 200，3 页 | 广东分项报价表；仅出现“格力”字样与一行报价，**无任何性能值** |
| `https://sx2gov2open2doc.uos.sxzfcg.zcygov.cn/.../7d40def2-....doc` | ✅ **HTTP 200** | 成功，灵石一中招标文件，含格力空气能机组门槛值 |
| `https://prec.sxzwfw.gov.cn/jyxxzczb/840060.jhtml` | ✅ HTTP 200 | 成功（中标公告正文，含 GN-53MDG/NaK 门槛行） |
| `http://ggzyjy.sxxz.gov.cn/ggDetailController.do?...noticeid=8a807e8c81ae824e0181f01601200028` | ✅ HTTP 200 | 成功（忻州煤改电分档门槛，只有名义制热量档位，无功率/COP） |
| `https://ggzy.qingdao.gov.cn/c/TradeDetals-ZtbShow/96970-...html` | HTTP 200 | 莱西特教中心中标公告：仅有“格力 GN-R43MLG/NaB1 5台 85500元”，**无性能参数** |
| `http://ggzyfw.beijing.gov.cn/cmsbj/u/cms/.../2731645513814.pdf`（雄忻高铁） | 未取到 | 检索摘要给出“格力投标报价561900元”，未取到含参数的原文，未采信 |
| `https://ec.sinopec.com/f/supp/notice/bidNotice.do?id=B9288A667CB486095450B2E8269E05A3` | 未取到 | 中石化中原油田评标公示（-13℃/33℃、45℃/55℃、COP≥2.45、R32），需登录/动态接口，未采信 |

---

## 四、文档站（依规则主动放弃，未绕付费墙）

| 站点 | 判定 |
|---|---|
| `max.book118.com`（原书 118） | 试读页 + 付费下载，**付费墙，放弃** |
| `wenku.csdn.net`（CSDN 文库） | 需订阅/积分，**付费墙，放弃** |
| `doc.mbalib.com`（MBA 智库） | 需注册/下载券，**付费墙，放弃** |
| 百度文库 / 道客巴巴 / 豆丁 | 同上，未尝试 |

---

## 五、下载了但与格力无关 / 不能用于本任务的文件

| 文件 | 说明 |
|---|---|
| `http://static.sse.com.cn/stock/disclosure/announcement/c/202103/000742_20210301_Q6V6.pdf` | 581 页，**广东芬尼科技（PHNIX）** 科创板问询回复，不是格力；其“制热量(KW)(环境20/15℃ 进水15~55℃)”表属芬尼自有产品，**未采信为格力数据**（已交回公共池，供 PHNIX 线使用） |

---

## 六、按需求逐项对照：没拿到的格力数据

1. **真正的二维表（行=室外 -30/-25/-20/-15/-12/-7/-2/0/7℃，列=出水 35/41/45/50/55/60℃，格=制热量/输入功率/COP）——任意一台格力商用机都没拿到。**
   公开渠道能拿到的只有“**档位**型”表：格力官方/经销商对外只印 3~4 个工况点（额定制热、名义制热、低温制热），不印逐环温 × 逐水温暖全网格。这是**数据源的物理上限**，不是检索不够。
2. **GN-R78ML/NaA、GN-R155ML/NaA 的 COP**：样本页只给 制热量 78/155 kW 与 制热额定功率 20.5/40.8 kW，**没有印 COP**，也没有印这两行对应的环温/出水温度。
3. **GN-R155ML/NaA 在 -30℃ 或出水 55℃ 的数据**：该机运行范围标注 `-25~45℃`、制热出水标称 `35~50℃`，**-30℃ 与 55℃ 均超出其标注范围**，公开源不可能有。
4. **格力 GN 系列的真·官方样本 PDF**：官网无下载入口；经销商下载链是空的；GTS 需登录 ⟹ **未取到**。
5. **水/地源热泵（水源侧水温 × 出水温度）二维表**：本次只拿到格力“水地源热泵”类目名与经销商 MS 系列一句“运行水源温度范围 -5℃~42℃”，**没有拿到任何水源侧 × 出水侧的性能表**。
6. **能效备案明细（备案号/能效等级/COP）**：中国能效标识网 502 不可达 ⟹ 未取到。
7. **检测报告（合肥通用机电产品检测院等）原文**：全网检索只命中标准与认证名录，**未检到任何一份格力的检测报告 PDF**。
8. `格力_HPKeymark_VersatiIII_14kW_EN14511_041-K004-06.pdf`（注册号 041-K004-06）：其 EN 14511-2 段落**排版被压成一列**（表头写“Bassa temperatura / Media temperatura”两档，但只抽出一个数值 13.50 kW / 5.19 kW / COP 2.60），无法判定该值属于哪一档出水温度 ⟹ **该 PDF 的性能值未录入 CSV**，只入库作型号佐证。
