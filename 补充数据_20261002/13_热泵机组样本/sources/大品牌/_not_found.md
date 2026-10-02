# 未获取清单（大品牌：格力 / 美的 / 海尔 / 天加 / 盾安 / 清华同方）

格式：`品牌 | 想找什么 | 试过的 URL | 失败原因`

## 格力 GREE
格力 | 官网 GN 系列"电子样本"PDF（产品页有"电子样本：点击下载"按钮） | http://bjdcjz.com.cn/index.php/Product/view/id/2969.html | 页面为 JS 触发的下载按钮，HTML 中无任何 href/文件路径；抓取到的页面只有产品描述文字，无性能表
格力 | 官网产品中心/资料下载栏目（商用低温热泵样本 PDF） | https://www.gree.com.cn/cmsProduct/view/1521 | HTTP 000（连接失败/超时，走代理与直连均失败）
格力 | CQC 格力 GN-HRZ11MDGVP/NhA 认证页原文 | https://www.cqc.com.cn/dynamic/contentcore/resource/download?ID=62428 | HTTP 404（返回 196 字节 HTML，非 PDF）
格力 | 国际站产品参数表 | https://global.gree.com/upload/files/2024/3/… | 仅欧盟 GMV 多联机，非商用低温热泵；未下载

## 美的 Midea
美的 | 官方商用热泵选型样本/技术手册（含环境温度×出水温度修正表） | https://www.midea.com.cn 、https://mbt.midea.com 检索 | 未找到 midea.com.cn 域下可公开下载的商用热泵样本；mbt.midea.com 上仅欧盟机型手册（T4/T1 限值表，非中国商用机组）
美的 | 美的烈焰系列官方样本 | http://www.huaruizhicheng.com.cn/t_chanpintuijie/productdetails.aspx?p_id=%2b172 | 页面仅有"点击图片进入"占位，无参数、无样本文件
美的 | 中国政府采购网美的热泵技术参数表 | ccgp.gov.cn 检索 | 命中 1 份（广东清远项目分项报价表），仅给出额定制热量 40.90kW / 额定功率 9.10kW，无 2 维表

## 海尔 Haier
海尔 | 官网"资料下载/服务支持"中的商用热泵样本 | https://www.haier.com/business/build/cpfa/rb/ 、https://www.haier.com/business/build/cpfa/rb/20260622_292205.shtml | 页面为 JS 渲染，抓到的 HTML 仅 2–3 KB 正文，无参数表；站内下载入口仅指向 /support/?goto=download（说明书/驱动，非产品样本）
海尔 | 星焰系列 30P/80P 完整工况表（A/W 交叉点） | https://www.hsf-365.com/product_view_700_413.html 、/product_view_699_413.html | 经销商页；参数表为 JS 相册内图片，HTML 中不含表格文本，页内亦无图片直链（仅 1 张备案图标）；未取到可核验文件
海尔 | 星焰 KD160-NcPB5-U1 性能参数（-12℃ 名义制热/功率） | 检索 haier.com / 政采公告 | 仅命中古交项目的 ★门槛值（≥102kW / ≤42.1kW），未找到厂家样本原始值
海尔 | 模块化空气源热泵机组 | https://www.eesia.cn/Uploads/file/20200522/1590141227890413.pdf | 已下载，但正文为宣传材料，无工况性能表（仅"COP最高3.60""-30℃可运行"等描述性文字）

## 天加 TICA
天加 | 中文官网 www.tica.com / www.tica.com.cn 样本下载 | https://www.tica.com 、https://www.tica.com.cn | 未取到中文商用低温热泵样本；本表数据取自 TICA 英文 V-FORCE 样本（dec.com.pk、tica.by 镜像）

## 盾安 Dunan
盾安 | FWRM 系列风冷涡旋（低环温型）技术手册 25.8MB | https://jiadianshuo.com/jdskongtiao/jdsdunan/540276601929285 | 付费墙：页面明确"支付宝扫码支付 1 元后点击下载按钮"，按规则未绕过
盾安 | 盾安全套产品手册 63.7MB / 46 页 | https://jiadianshuo.com/jdskongtiao/jdsdunan/795660790491652 | 同上，付费墙
盾安 | 官网产品页内嵌样本 PDF | http://www.dunanac.com/product_info.aspx?id=571&typeid=14&cid=17 | 产品介绍页无参数表、无样本附件链接

## 清华同方
清华同方 | 官网 www.thrh.com.cn 样本下载 | https://www.thrh.com.cn | 未能获取到公开可下载样本（域名/栏目未命中）
清华同方 | 艾默生密云煤改电案例 PDF（含 HSYR-DG-18(D)BPIII 实测 14.4kW/COP 2.34） | https://www.emerson.cn/documents/commercial-residential/case-study-copeland-scroll-variable-speed-zww-for-heating-zh-cn-5480320.pdf | HTTP 404（返回 HTML 错误页），已删除无效文件
清华同方 | 华瑞志诚"清华同方空气源热泵样本"资料 | https://www.huaruizhicheng.com/sqfw/chakan.aspx?leibie=1&fenlei=1 | 资料列表页需分级权限/登录才能下载，未绕过

## 通用
全部品牌 | CQC 低环境温度空气源热泵（冷水）机组认证名录中提取型号佐证 | https://www.cqc.com.cn/www/upload/files/upload/resources/file/2022/04/25/41938.pdf 、https://www.cqc.com.cn/www/upload/file/2025-12/1766479076697.pdf | PDF 中文字体未嵌入（pdftotext 抽取为乱码/空），需逐页 OCR；且名录**只有型号、无制热量/功率/COP**，对性能表无贡献，故未逐页 OCR

## 补充失败记录（2026-10-02 追加）
品牌 | 想找什么 | 试过的 URL | 失败原因
--- | --- | --- | ---
天加 | 中文官网样本/资料下载 | https://www.tica.com.cn | HTTP 000 连接失败
天加 | 中文官网"超低环温空气源热泵采暖机组"参数表 | https://www.tica.com/index/goods/pro/cid/233.html | 页面仅宣传文字（"最低制热运行环温可达-32℃""最高出水温度可达60℃"），无型号参数表、无样本附件
天加 | 中文官网资料下载 | https://www.tica.com/file/2023/TICA2022.pdf | 该链接为2022年度报告，非产品样本
盾安 | 官网域名 | https://www.dunan.com | HTTP 000 连接失败；实际官网为 www.dunanac.com（产品页无参数表、无样本附件）
清华同方 | 官网 | http://www.thrh.com.cn | HTTP 502 Bad Gateway
海尔 | 官网赤焰+系列页 | https://www.haier.com/business/build/cpfa/rb/20260622_292205.shtml | 抓到的HTML仅约3KB正文，参数表由JS异步加载，未取到
海尔 | 官网热泵产品列表页 | https://www.haier.com/business/build/cpfa/rb/ | 同上，无参数、无样本文件
海尔 | 星焰系列经销商页参数表 | https://hsf-365.com/product_view_699_413.html 、/product_view_700_413.html 、/product_view_701_413.html | 页面HTML内无表格文本，无产品图直链（仅1张备案图标），参数表在JS相册图片中，未取到可核验文件
美的 | 官方EU ERP技术参数表 MHA-V16W/D2N1 | https://mbt.midea.com/content/dam/midea-aem/mbt/support/erp-directive/lot-1/P020170823578776473417.pdf | 已下载（49页），但逐页未见与 Mars/AquaThermalSuper 同格式的 Pdh/COPd 工况表，未逐页抽值
美的 | 官方EU产品参数表（Mars / AquaThermalSuper）第5–7页其余表 | 同上两份PDF | 第5–7页缺"low/medium temperature application"分节标题，列↔型号映射无法可靠确定，为保证不误标故未收入CSV（文件已存档）

## 格力补记（2026-10-02 追加）
品牌 | 想找什么 | 试过的 URL | 失败原因
--- | --- | --- | ---
格力 | 官网商用低温热泵样本 PDF | https://www.gree.com.cn/cmsProduct/view/1521 （先后 3 次，含 --compressed 重试） | HTTP 000，连接超时；直连与代理均失败
格力 | 官网 GN 系列"电子样本：点击下载"文件 | http://bjdcjz.com.cn/index.php/Product/view/id/2969.html | 按钮由 JS 触发，HTML 内无任何 href/文件路径；页面本身只有营销文字，无性能表
格力 | 格力欧盟参数表（作为替代样本） | https://global.gree.com/upload/files/2024/3/…GMV-160WLC-X.pdf | 已下载成功，但该件为 **air-to-air（多联/风管）** 机组，只有室内20℃侧的 Tj-Pdh/COPd 曲线，**没有出水温度轴**，不符合本题二维表口径，故未写入 CSV
格力 | 格力 CQC 认证名录中的性能值 | https://www.cqc.com.cn/www/upload/files/... （2022版 / 2025-12版，均已下载） | 名录**只有证书号与型号**（如 GN-HRZ11MDGVP/NhA、GN-R155ML/NaA 额定出水温度41℃），无制热量/功率/COP
格力 | 含完整"环境温度×出水温度"二维表的格力件 | 多轮检索 ccgp / 各省公共资源交易 / 招标网 | 未命中；格力公开件普遍只给单点名义值（≥98kW/≤23kW 之类）或双温度点（7℃/-12℃ 制热量）
