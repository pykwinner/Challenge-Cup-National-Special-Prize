from __future__ import annotations

import argparse
import calendar
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "public_sources"
DEFAULT_YEAR = 2023
EXTENSIONS = ROOT / "data" / "public_source_extensions"


LOCATIONS = {
    "scenario1_urumqi": {
        "scenario": "scenario1_existing_network",
        "city": "乌鲁木齐",
        "latitude": 43.8256,
        "longitude": 87.6168,
        "role": "既有大型集中供热系统低碳化重构代表城市",
    },
    "scenario2_guyuan": {
        "scenario": "scenario2_distributed_clean",
        "city": "固原",
        "latitude": 36.0159,
        "longitude": 106.2426,
        "role": "集中与分散并存、农村散煤清洁替代代表地区",
    },
    "scenario3_wuhan": {
        "scenario": "scenario3_high_quality_newbuild",
        "city": "武汉",
        "latitude": 30.5928,
        "longitude": 114.3055,
        "role": "夏热冬冷地区按需、多能互补供热代表城市",
    },
}


SOURCES = [
    {
        "source_id": "S1_URUMQI_PLAN_2025",
        "scenario": "scenario1_existing_network",
        "category": "供热规划/热源/管网/余热",
        "publisher": "乌鲁木齐市人民政府",
        "title": "乌鲁木齐城市供热高质量发展规划（2025—2027年）（草案）",
        "url": "https://www.urumqi.gov.cn/wlmqs/c119242/202506/282e876f9e074a4f837ea68ae7b30f59.shtml",
        "data_year": "2025-2027规划",
        "authority": "地方政府",
        "use_in_model": "热源容量、供热面积、联网扩展、燃气锅炉整合、工业余热场景",
        "caveat": "规划草案；正式应用需核对最终批复与企业台账",
    },
    {
        "source_id": "S1_URUMQI_GROUP_2025",
        "scenario": "scenario1_existing_network",
        "category": "企业资产现状",
        "publisher": "乌鲁木齐市人民政府",
        "title": "乌鲁木齐热力（集团）有限公司简介",
        "url": "https://www.urumqi.gov.cn/wlmqs/c119333/202506/464a83f173fe457584ff216a7b1b2f6e.shtml",
        "data_year": "截至页面发布",
        "authority": "地方政府",
        "use_in_model": "管网长度、换热站、锅炉房、供热面积和用户规模",
        "caveat": "仅覆盖集团运营范围，不代表全市全部供热资产",
    },
    {
        "source_id": "S1_URUMQI_14FIVE",
        "scenario": "scenario1_existing_network",
        "category": "能源结构情景",
        "publisher": "乌鲁木齐市人民政府",
        "title": "乌鲁木齐市供热行业十四五相关规划答复",
        "url": "https://www.urumqi.gov.cn/wlmqs/c128188/202206/81cda9af792c48e9baa12516e5188bb0.shtml",
        "data_year": "十四五",
        "authority": "地方政府",
        "use_in_model": "天然气、热电联产、电采暖、工业余热替代边界",
        "caveat": "规划估算口径，需与统计年鉴和供热企业实测量交叉验证",
    },
    {
        "source_id": "S1_URUMQI_SMART_2023",
        "scenario": "scenario1_existing_network",
        "category": "智慧供热公开招标",
        "publisher": "中国政府采购网",
        "title": "乌鲁木齐热力（集团）有限公司智慧供热项目（EPC）",
        "url": "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202307/t20230728_20394380.htm",
        "data_year": "2023-2025建设期",
        "authority": "政府采购公告",
        "use_in_model": "智慧化投资、锅炉房/换热站/用户/管网规模边界",
        "caveat": "预算和初设服务范围不等于最终结算及实际完成量",
    },
    {
        "source_id": "S1_URUMQI_GAS_PRICE_2024",
        "scenario": "scenario1_existing_network",
        "category": "燃气价格",
        "publisher": "乌鲁木齐市发展和改革委员会",
        "title": "关于理顺乌鲁木齐市管道天然气销售价格的通知",
        "url": "https://www.urumqi.gov.cn/wlmqs/c121051/202406/8e0f310cdf5f491bb903a20d572dc06e.shtml",
        "data_year": "2024",
        "authority": "地方价格主管部门",
        "use_in_model": "燃气锅炉燃料成本与价格敏感性",
        "caveat": "终端气价需结合锅炉效率和燃气低位热值换算为热成本",
    },
    {
        "source_id": "S1_URUMQI_HEAT_PRICE_2024",
        "scenario": "scenario1_existing_network",
        "category": "供热价格",
        "publisher": "乌鲁木齐市人民政府",
        "title": "供热公示信息",
        "url": "https://www.urumqi.gov.cn/wlmqs/c119242/202408/791c272b46d8454cad0a2af73e8ecbbf.shtml",
        "data_year": "2024",
        "authority": "地方政府价格公示",
        "use_in_model": "热价约束、收入和财务模型",
        "caveat": "转供热价和终端面积热价口径不同，不能直接互换",
    },
    {
        "source_id": "S1_JINAN_HEATPUMP_2026",
        "scenario": "scenario1_existing_network",
        "category": "大型电热泵工程造价",
        "publisher": "济南市行政审批服务局",
        "title": "外热入济配套西客站能源站供热项目核准批复",
        "url": "https://jnzwfw.jinan.gov.cn/col/col82909/art/2026/art_9e675fa55e884a9aa744404a000b424d.html",
        "data_year": "2026",
        "authority": "项目核准批复",
        "use_in_model": "大型电热泵容量、管径、管长和总投资交叉校准",
        "caveat": "总投资包含建筑、管网、设备改造和附属系统，不能全部计为热泵设备单价",
    },
    {
        "source_id": "S1_BEIAN_NETWORK_PLAN",
        "scenario": "scenario1_existing_network",
        "category": "骨干管网扩建案例",
        "publisher": "北安市人民政府",
        "title": "北安市中心城区热电联产规划（2024—2035年）",
        "url": "https://www.hljba.gov.cn/bas/c101164/202412/c11_313186.shtml",
        "data_year": "2024-2035规划",
        "authority": "地方政府规划公开",
        "use_in_model": "DN1400骨干管网、扩建长度和互联互备场景",
        "caveat": "异地工程案例，仅用于管网尺度和情景边界",
    },
    {
        "source_id": "S2_GUYUAN_PLAN",
        "scenario": "scenario2_distributed_clean",
        "category": "分散清洁取暖规划",
        "publisher": "固原市人民政府",
        "title": "固原市清洁取暖专项规划",
        "url": "https://www.nxgy.gov.cn/zwgk/zcwj/qtwj/jgzn_35170/202208/W020220822623460812528.pdf",
        "data_year": "2021-2025规划",
        "authority": "地方政府",
        "use_in_model": "热指标、县城/农村改造面积、热泵、生物质、气网和热网项目",
        "caveat": "部分项目技术名称需按现行国家能效标准重新校准",
    },
    {
        "source_id": "S2_NATIONAL_CLEAN_HEAT",
        "scenario": "scenario2_distributed_clean",
        "category": "国家技术路线",
        "publisher": "国家发展改革委等部门",
        "title": "北方地区冬季清洁取暖规划（2017—2021年）",
        "url": "https://www.ndrc.gov.cn/xxgk/zcfb/tz/201712/W020231016553311864493.pdf",
        "data_year": "2017-2021",
        "authority": "国家部委",
        "use_in_model": "生物质分散供暖、热泵、电供暖和区域选择原则",
        "caveat": "政策期已结束，仅作为技术路线和历史基准",
    },
    {
        "source_id": "S2_GUYUAN_STRAW_2024",
        "scenario": "scenario2_distributed_clean",
        "category": "生物质资源量",
        "publisher": "固原市人民政府",
        "title": "2024年度固原市固体废物污染环境防治信息",
        "url": "https://www.nxgy.gov.cn/zwgk/zfxxgkml/jgzn_38938/hjbh/hbgs/202505/t20250530_4923067.html",
        "data_year": "2024",
        "authority": "地方政府年度信息",
        "use_in_model": "生物质年度产生、可收集和竞争利用上限",
        "caveat": "可收集量不等于可供供热量，必须扣除饲料化、肥料化等既有用途",
    },
    {
        "source_id": "S2_BIOMASS_PURCHASE_2025",
        "scenario": "scenario2_distributed_clean",
        "category": "生物质燃料采购",
        "publisher": "全国公共资源交易平台（辽宁省·本溪市）",
        "title": "2025年冬季供暖生物质颗粒采购公告",
        "url": "https://ggzyjy.benxi.gov.cn/jyxx/003002/003002001/20251013/68f419ba-0b30-48c6-9a50-a4d1317ba95a.html",
        "data_year": "2025",
        "authority": "政府采购公告",
        "use_in_model": "颗粒燃料热值、采购价和批量",
        "caveat": "异地到场最高限价，运输距离和本地供需会显著改变价格",
    },
    {
        "source_id": "S2_PENGYANG_SUBSIDY_2023",
        "scenario": "scenario2_distributed_clean",
        "category": "农村清洁取暖补贴",
        "publisher": "彭阳县人民政府",
        "title": "彭阳县2023年农村冬季清洁取暖项目专项实施方案",
        "url": "https://www.pengyang.gov.cn/xxgk_13872/zc/wjfb/xzfwj/202309/t20230901_4246165.html",
        "data_year": "2023",
        "authority": "县级政府实施方案",
        "use_in_model": "生物质炉具和空气源热泵补贴、居民负担情景",
        "caveat": "补贴额不是设备完整寿命周期成本，且政策年度可能变化",
    },
    {
        "source_id": "S2_DATONG_ASHP_2023",
        "scenario": "scenario2_distributed_clean",
        "category": "农村煤改电工程",
        "publisher": "青海省公共资源交易网",
        "title": "大通县冬季清洁取暖热源侧煤改电项目合同",
        "url": "https://www.qhdzzbfw.gov.cn/ggzy/fpgj/012005/20240513/8eb30e3d-cf77-46c1-82be-819f3aa2d3e6.html",
        "data_year": "2023-2024",
        "authority": "公共资源交易合同公告",
        "use_in_model": "户级空气源热泵节点数量、典型供热面积和配套电热设备",
        "caveat": "高海拔异地项目，设备容量与气候修正不可直接照搬固原",
    },
    {
        "source_id": "S3_WUHAN_ENERGY_STATION",
        "scenario": "scenario3_high_quality_newbuild",
        "category": "区域能源站案例",
        "publisher": "武汉市人民政府",
        "title": "中法武汉生态示范城启动区能源站",
        "url": "https://www.wuhan.gov.cn/sy/whyw/202208/t20220808_2019549.shtml",
        "data_year": "2022",
        "authority": "地方政府",
        "use_in_model": "区域能源站面积、空气源热泵主供与燃气多能互补结构",
        "caveat": "新闻案例，不包含完整逐时运行和造价台账",
    },
    {
        "source_id": "S3_WUHAN_EFFICIENCY_2025",
        "scenario": "scenario3_high_quality_newbuild",
        "category": "设备能效",
        "publisher": "武汉市节能监察中心",
        "title": "武汉产业能效指南（2025版）",
        "url": "https://fgw.wuhan.gov.cn/zfxxgk/zfxxgk_1/qtzdgkwj/202507/P020250815407653042237.pdf",
        "data_year": "2025",
        "authority": "地方政府技术指南",
        "use_in_model": "低环境温度空气源热泵与水地源热泵COP/HSPF边界",
        "caveat": "用于设备准入和情景边界，逐时COP仍需厂家曲线与实测数据",
    },
    {
        "source_id": "S3_WUHAN_APPROVAL_2020",
        "scenario": "scenario3_high_quality_newbuild",
        "category": "区域能源站初步设计",
        "publisher": "武汉市蔡甸区人民政府",
        "title": "中法武汉生态示范城启动区能源站初步设计批复",
        "url": "https://www.caidian.gov.cn/zwgk/xxgkml/zdxm/xmjys/202012/t20201201_1523964.shtml",
        "data_year": "2020",
        "authority": "项目初步设计批复",
        "use_in_model": "供冷供热容量、设备组合、光伏、管网长度和建筑面积",
        "caveat": "初步设计参数需用竣工和运行数据复核",
    },
    {
        "source_id": "S3_WUHAN_OPERATION_2023",
        "scenario": "scenario3_high_quality_newbuild",
        "category": "能源站运行边界",
        "publisher": "武汉市蔡甸区人民政府",
        "title": "武汉首座能源站基本建成",
        "url": "https://www.caidian.gov.cn/zwgk/zwdt/cdxw/202301/t20230106_2126240.shtml",
        "data_year": "2023",
        "authority": "地方政府项目公开",
        "use_in_model": "供水温度、可再生能源利用率、一期投资和管网长度",
        "caveat": "新闻披露的设计指标不等于完整采暖季实测绩效",
    },
    {
        "source_id": "S3_GB19577_2024",
        "scenario": "scenario3_high_quality_newbuild",
        "category": "热泵国家能效标准",
        "publisher": "国家市场监督管理总局、国家标准化管理委员会",
        "title": "GB 19577—2024 热泵和冷水机组能效限定值及能效等级",
        "url": "https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=C59327F9E5A3194DAD64C39B883C36C2&refer=outter",
        "data_year": "2024发布/2025实施",
        "authority": "现行国家标准",
        "use_in_model": "候选热泵工程准入和能效底线",
        "caveat": "标准限定值需结合具体产品类型、名义制热量和末端形式读取",
    },
    {
        "source_id": "S3_NEAR_ZERO_STANDARD",
        "scenario": "scenario3_high_quality_newbuild",
        "category": "新建建筑节能情景",
        "publisher": "深圳市住房和建设局（引用GB/T 51350—2019）",
        "title": "近零能耗建筑技术标准解释",
        "url": "https://www.sz.gov.cn/ztfw/zzrd/wyw/ywzsk/content/post_12670365.html",
        "data_year": "现行标准解释",
        "authority": "地方政府引用国家标准",
        "use_in_model": "新建建筑需求下降敏感性边界",
        "caveat": "60%—75%是相对基准标准降幅，不是任意存量建筑直接降幅",
    },
    {
        "source_id": "S3_HEAT_PUMP_ACTION_2025",
        "scenario": "scenario3_high_quality_newbuild",
        "category": "国家热泵政策",
        "publisher": "国家发展改革委等部门",
        "title": "推动热泵行业高质量发展行动方案",
        "url": "https://fgw.pingliang.gov.cn/ywgz/zyhj/art/2025/art_960fad75af474efd8f04634f81736d46.html",
        "data_year": "2025",
        "authority": "国家部委文件转载",
        "use_in_model": "夏热冬冷地区空气源、地源、水源和余热源热泵候选集",
        "caveat": "政策方向不直接给出城市级容量或成本",
    },
    {
        "source_id": "COMMON_CMA",
        "scenario": "common",
        "category": "气象观测",
        "publisher": "中国气象局国家气象信息中心",
        "title": "中国地面基本气象观测数据",
        "url": "https://m.data.cma.cn/data/cdcindex/cid/6d1b5efbdcbf9a58.html",
        "data_year": "长期序列",
        "authority": "国家部委",
        "use_in_model": "气温、相对湿度、风速；正式参赛气象基准",
        "caveat": "部分数据下载需要注册；站点选择和缺测处理需人工复核",
    },
    {
        "source_id": "COMMON_NASA_POWER",
        "scenario": "common",
        "category": "逐小时气象替代数据",
        "publisher": "NASA POWER",
        "title": "NASA POWER Hourly API",
        "url": "https://power.larc.nasa.gov/docs/services/api/temporal/hourly/",
        "data_year": str(DEFAULT_YEAR),
        "authority": "政府科研数据服务",
        "use_in_model": "自动抓取三地逐小时温度、湿度、风速和太阳辐射原型数据",
        "caveat": "格点/再分析数据，只用于原型；正式结论应以CMA地面站校准",
    },
    {
        "source_id": "COMMON_GRID_EF_2023",
        "scenario": "common",
        "category": "电力碳排放",
        "publisher": "生态环境部",
        "title": "2023年电力二氧化碳排放因子",
        "url": "https://www.mee.gov.cn/xxgk2018/xxgk/xxgk01/202512/W020251231726284332528.pdf",
        "data_year": "2023",
        "authority": "国家部委",
        "use_in_model": "grid_emission_factor基准",
        "caveat": "全国平均值；省级/区域电网和绿电交易应另行建模",
    },
]


FACTS = [
    # Scenario 1: Urumqi existing network.
    ("S1_URUMQI_PLAN_2025", "scenario1_existing_network", "热电厂现状可对外供热能力", 2278, "MW", "sources.existing_capacity_kw", "直接事实"),
    ("S1_URUMQI_PLAN_2025", "scenario1_existing_network", "当前接入电厂区域供热面积", 10000, "万平方米", "城市规模校准", "直接事实"),
    ("S1_URUMQI_PLAN_2025", "scenario1_existing_network", "河滩路以东区域供热面积", 12000, "万平方米", "nodes/demand分区", "直接事实"),
    ("S1_URUMQI_PLAN_2025", "scenario1_existing_network", "优化联网后初末寒电厂可覆盖面积", 14000, "万平方米", "pipes/seasonal scenario", "规划测算"),
    ("S1_URUMQI_PLAN_2025", "scenario1_existing_network", "河滩路以西新增联通供热面积", 2000, "万平方米", "pipes.diameter_options", "规划测算"),
    ("S1_URUMQI_PLAN_2025", "scenario1_existing_network", "拟集约化整合小型燃气锅炉房", 229, "座", "source_retirement/aggregation", "规划任务"),
    ("S1_URUMQI_GROUP_2025", "scenario1_existing_network", "集团供热管网长度", 1299, "km", "pipes.length_m", "企业现状"),
    ("S1_URUMQI_GROUP_2025", "scenario1_existing_network", "集团热交换站", 1317, "座", "nodes/substations", "企业现状"),
    ("S1_URUMQI_GROUP_2025", "scenario1_existing_network", "集团燃气锅炉房", 185, "座", "sources", "企业现状"),
    ("S1_URUMQI_GROUP_2025", "scenario1_existing_network", "集团供热面积", 10100, "万平方米", "demand scaling", "企业现状"),
    ("S1_URUMQI_GROUP_2025", "scenario1_existing_network", "服务热用户", 71, "万户", "customer nodes", "企业现状"),
    ("S1_URUMQI_14FIVE", "scenario1_existing_network", "天然气供热需求情景", 21, "亿立方米", "fuel/cost scenario", "规划情景"),
    ("S1_URUMQI_14FIVE", "scenario1_existing_network", "热电联产供热量情景", 2000, "万GJ", "CHP annual heat", "规划情景"),
    ("S1_URUMQI_SMART_2023", "scenario1_existing_network", "智慧供热项目预算", 69600, "万元", "digitalization capex", "采购预算"),
    ("S1_URUMQI_SMART_2023", "scenario1_existing_network", "智慧平台支撑燃气锅炉房下限", 200, "座", "source monitoring nodes", "初设范围"),
    ("S1_URUMQI_SMART_2023", "scenario1_existing_network", "智慧平台支撑换热站下限", 2500, "座", "substation nodes", "初设范围"),
    ("S1_URUMQI_SMART_2023", "scenario1_existing_network", "智慧平台支撑换热站上限", 3000, "座", "substation nodes", "初设范围"),
    ("S1_URUMQI_SMART_2023", "scenario1_existing_network", "智慧平台支撑用户下限", 100, "万户", "customer nodes", "初设范围"),
    ("S1_URUMQI_SMART_2023", "scenario1_existing_network", "智慧平台一次管网下限", 500, "km", "pipes length", "初设范围"),
    ("S1_URUMQI_SMART_2023", "scenario1_existing_network", "智慧平台一次管网上限", 800, "km", "pipes length", "初设范围"),
    ("S1_URUMQI_SMART_2023", "scenario1_existing_network", "二网平衡覆盖面积下限", 5000, "万平方米", "demand response coverage", "初设范围"),
    ("S1_URUMQI_GAS_PRICE_2024", "scenario1_existing_network", "居民天然气第一阶梯价格", 1.50, "元/立方米", "fuel price", "政府定价"),
    ("S1_URUMQI_GAS_PRICE_2024", "scenario1_existing_network", "集中供热天然气价格", 1.70, "元/立方米", "fuel price", "政府定价"),
    ("S1_URUMQI_GAS_PRICE_2024", "scenario1_existing_network", "工商业天然气价格", 2.50, "元/立方米", "fuel price scenario", "政府定价"),
    ("S1_URUMQI_HEAT_PRICE_2024", "scenario1_existing_network", "热源至换热站入口转供热价格", 24.5, "元/GJ", "tariff/revenue", "政府定价"),
    ("S1_URUMQI_HEAT_PRICE_2024", "scenario1_existing_network", "非居民集中供热面积价格", 31, "元/平方米采暖期", "tariff/revenue", "政府指导价"),
    ("S1_JINAN_HEATPUMP_2026", "scenario1_existing_network", "新增大型电热泵数量", 4, "套", "source count", "核准规模"),
    ("S1_JINAN_HEATPUMP_2026", "scenario1_existing_network", "单套大型电热泵容量", 11, "MW", "source capacity", "核准规模"),
    ("S1_JINAN_HEATPUMP_2026", "scenario1_existing_network", "大型电热泵能源站总投资", 7805, "万元", "system capex", "核准投资"),
    ("S1_JINAN_HEATPUMP_2026", "scenario1_existing_network", "泵房内DN500-DN700热力管道", 168, "m", "pipes", "核准规模"),
    ("S1_JINAN_HEATPUMP_2026", "scenario1_existing_network", "厂区DN700高温水管道", 780, "m", "pipes", "核准规模"),
    ("S1_BEIAN_NETWORK_PLAN", "scenario1_existing_network", "2035集中供热面积", 1182, "万平方米", "demand scale", "规划目标"),
    ("S1_BEIAN_NETWORK_PLAN", "scenario1_existing_network", "DN1400原有干线改造长度", 12.5, "km", "backbone retrofit", "规划项目"),
    ("S1_BEIAN_NETWORK_PLAN", "scenario1_existing_network", "远期新建一级管网", 11.8, "km", "pipes", "规划项目"),
    # Scenario 2: Guyuan dispersed transition.
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "城区清洁取暖改造面积目标", 286, "万平方米", "demand coverage", "规划目标"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "县城清洁取暖改造面积目标", 832, "万平方米", "demand coverage", "规划目标"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "现状综合热指标", 59, "W/平方米", "peak demand", "规划采用值"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "规划综合热指标", 46.5, "W/平方米", "peak demand", "规划采用值"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "2025城区县城集中供热负荷", 1590.16, "MW", "firm capacity", "规划测算"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "原州区乡镇燃气中压管道", 240, "km", "infrastructure scenario", "规划项目"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "乡镇天然气入户", 4955, "户", "customer conversion", "规划项目"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "无集中供热区煤改气户数", 6000, "户", "distributed nodes", "规划项目"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "无集中供热区煤改气面积", 62, "万平方米", "distributed demand", "规划项目"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "村委会生物质颗粒锅炉", 245, "台", "biomass sources", "规划项目"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "生物质颗粒锅炉覆盖面积", 4.9, "万平方米", "biomass demand", "规划项目"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "农村分散供热清洁化改造", 2.4, "万户", "distributed transition", "规划项目"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "规划新建供热管网", 22.45, "km", "pipes", "规划项目"),
    ("S2_GUYUAN_PLAN", "scenario2_distributed_clean", "规划改造供热管网", 43.75, "km", "pipes", "规划项目"),
    ("S2_GUYUAN_STRAW_2024", "scenario2_distributed_clean", "农作物秸秆产生量", 828132.02, "吨/年", "biomass resource", "年度统计"),
    ("S2_GUYUAN_STRAW_2024", "scenario2_distributed_clean", "农作物秸秆可收集量", 760893.04, "吨/年", "annual_supply_limit_kg upper bound", "年度统计"),
    ("S2_GUYUAN_STRAW_2024", "scenario2_distributed_clean", "农作物秸秆利用量", 704025.30, "吨/年", "competing use", "年度统计"),
    ("S2_GUYUAN_STRAW_2024", "scenario2_distributed_clean", "农作物秸秆综合利用率", 92.53, "%", "resource availability scenario", "年度统计"),
    ("S2_GUYUAN_STRAW_2024", "scenario2_distributed_clean", "秸秆可收集量减既有利用量", 56867.74, "吨/年", "conservative incremental supply ceiling", "派生上限"),
    ("S2_BIOMASS_PURCHASE_2025", "scenario2_distributed_clean", "生物质颗粒采购量", 360, "吨", "procurement batch", "采购需求"),
    ("S2_BIOMASS_PURCHASE_2025", "scenario2_distributed_clean", "生物质颗粒最高限价", 1250, "元/吨", "procurement_cost_yuan_per_kg", "采购限价"),
    ("S2_BIOMASS_PURCHASE_2025", "scenario2_distributed_clean", "生物质颗粒收到基低位热值下限", 4200, "kcal/kg", "lower_heating_value_kwh_per_kg", "采购技术条件"),
    ("S2_PENGYANG_SUBSIDY_2023", "scenario2_distributed_clean", "生物质专用炉具前端补贴", 3150, "元/户", "capex subsidy", "补贴标准"),
    ("S2_PENGYANG_SUBSIDY_2023", "scenario2_distributed_clean", "3P空气源热泵热水机前端补贴", 7840, "元/户", "capex subsidy", "补贴标准"),
    ("S2_PENGYANG_SUBSIDY_2023", "scenario2_distributed_clean", "5P及以上空气源热泵前端补贴", 9070, "元/户", "capex subsidy", "补贴标准"),
    ("S2_DATONG_ASHP_2023", "scenario2_distributed_clean", "煤改电项目折算户数", 1125, "户", "distributed nodes", "合同规模"),
    ("S2_DATONG_ASHP_2023", "scenario2_distributed_clean", "户级典型供热面积", 80, "平方米/户", "node demand scale", "合同口径"),
    # Scenario 3: Wuhan hot-summer/cold-winter newbuild.
    ("S3_WUHAN_ENERGY_STATION", "scenario3_high_quality_newbuild", "区域能源站服务建筑面积", 103, "万平方米", "district demand scale", "示范项目"),
    ("S3_WUHAN_ENERGY_STATION", "scenario3_high_quality_newbuild", "空气源热泵是否为冬季核心设备", 1, "布尔", "source candidate", "示范项目"),
    ("S3_WUHAN_EFFICIENCY_2025", "scenario3_high_quality_newbuild", "低温空气源热泵地板采暖型小机组准入HSPF", 2.8, "W/W", "cop_min/seasonal", "能效准入"),
    ("S3_WUHAN_EFFICIENCY_2025", "scenario3_high_quality_newbuild", "低温空气源热泵地板采暖型小机组先进HSPF", 3.6, "W/W", "cop scenario", "先进水平"),
    ("S3_WUHAN_EFFICIENCY_2025", "scenario3_high_quality_newbuild", "水环式水地源热泵准入ACOP", 3.7, "W/W", "cop_min", "能效准入"),
    ("S3_WUHAN_EFFICIENCY_2025", "scenario3_high_quality_newbuild", "水环式水地源热泵先进ACOP", 4.6, "W/W", "cop_max scenario", "先进水平"),
    ("S3_WUHAN_APPROVAL_2020", "scenario3_high_quality_newbuild", "能源站设计供冷能力", 29.75, "MW", "cooling co-design", "初步设计"),
    ("S3_WUHAN_APPROVAL_2020", "scenario3_high_quality_newbuild", "能源站设计供热能力", 29.60, "MW", "firm heat capacity", "初步设计"),
    ("S3_WUHAN_APPROVAL_2020", "scenario3_high_quality_newbuild", "能源站供能面积", 103.27, "万平方米", "demand scale", "初步设计"),
    ("S3_WUHAN_APPROVAL_2020", "scenario3_high_quality_newbuild", "燃气内燃机组数量", 2, "套", "CCHP sources", "初步设计"),
    ("S3_WUHAN_APPROVAL_2020", "scenario3_high_quality_newbuild", "单套燃气内燃机容量", 1560, "kW", "CCHP capacity", "初步设计"),
    ("S3_WUHAN_APPROVAL_2020", "scenario3_high_quality_newbuild", "7MW燃气热水锅炉数量", 2, "台", "backup sources", "初步设计"),
    ("S3_WUHAN_APPROVAL_2020", "scenario3_high_quality_newbuild", "空气源热泵数量", 8, "台", "heat pump sources", "初步设计"),
    ("S3_WUHAN_APPROVAL_2020", "scenario3_high_quality_newbuild", "单台空气源热泵容量", 1583, "kW", "heat pump capacity", "初步设计"),
    ("S3_WUHAN_APPROVAL_2020", "scenario3_high_quality_newbuild", "屋顶光伏容量", 150, "kW", "renewable electricity", "初步设计"),
    ("S3_WUHAN_APPROVAL_2020", "scenario3_high_quality_newbuild", "新建供热供冷管网", 11.23, "km", "pipes", "初步设计"),
    ("S3_WUHAN_OPERATION_2023", "scenario3_high_quality_newbuild", "冬季供水温度下限", 50, "摄氏度", "low grade temperature", "项目公开"),
    ("S3_WUHAN_OPERATION_2023", "scenario3_high_quality_newbuild", "冬季供水温度上限", 55, "摄氏度", "low grade temperature", "项目公开"),
    ("S3_WUHAN_OPERATION_2023", "scenario3_high_quality_newbuild", "可再生能源利用率下限", 60, "%", "clean heat policy", "设计指标"),
    ("S3_WUHAN_OPERATION_2023", "scenario3_high_quality_newbuild", "一期工程投资", 2.4, "亿元", "system capex", "项目公开"),
    ("S3_NEAR_ZERO_STANDARD", "scenario3_high_quality_newbuild", "近零能耗建筑相对基准能耗降低下限", 60, "%", "demand reduction scenario", "标准解释"),
    ("S3_NEAR_ZERO_STANDARD", "scenario3_high_quality_newbuild", "近零能耗建筑相对基准能耗降低上限", 75, "%", "demand reduction scenario", "标准解释"),
    # Common.
    ("COMMON_GRID_EF_2023", "common", "全国电力平均二氧化碳排放因子", 0.5306, "kgCO2/kWh", "grid_emission_factor", "官方因子"),
]


def fetch_power(location: dict, year: int) -> tuple[pd.DataFrame, str]:
    parameters = {
        "parameters": "T2M,RH2M,WS10M,ALLSKY_SFC_SW_DWN",
        "community": "RE",
        "longitude": location["longitude"],
        "latitude": location["latitude"],
        "start": f"{year}0101",
        "end": f"{year}1231",
        "format": "JSON",
        "time-standard": "LST",
    }
    url = "https://power.larc.nasa.gov/api/temporal/hourly/point?" + urlencode(parameters)
    request = Request(url, headers={"User-Agent": "heatplan-public-data/1.0"})
    with urlopen(request, timeout=180) as response:
        payload = json.load(response)
    raw = payload["properties"]["parameter"]
    keys = sorted(raw["T2M"])
    frame = pd.DataFrame({
        "timestamp_local_solar": pd.to_datetime(keys, format="%Y%m%d%H"),
        "outdoor_temp": [raw["T2M"][key] for key in keys],
        "relative_humidity_pct": [raw["RH2M"][key] for key in keys],
        "wind_speed_10m_m_s": [raw["WS10M"][key] for key in keys],
        "solar_irradiance_kwh_m2_hour": [raw["ALLSKY_SFC_SW_DWN"][key] for key in keys],
    })
    frame = frame.replace(-999.0, np.nan)
    frame["relative_humidity"] = frame["relative_humidity_pct"] / 100.0
    frame["heating_degree_c"] = (18.0 - frame["outdoor_temp"]).clip(lower=0.0)
    solar_scale = float(frame["solar_irradiance_kwh_m2_hour"].quantile(0.95))
    frame["solar_heat_availability"] = (
        frame["solar_irradiance_kwh_m2_hour"] / max(solar_scale, 1e-9)
    ).clip(0.0, 1.0)
    frame.insert(0, "time", frame["timestamp_local_solar"].dt.strftime("%Y-%m-%d %H:%M:%S"))
    expected_rows = 8784 if calendar.isleap(year) else 8760
    critical_columns = ["outdoor_temp", "relative_humidity", "wind_speed_10m_m_s"]
    if len(frame) != expected_rows:
        raise RuntimeError(f"{location['city']} {year}年气象数据应为{expected_rows}行，实际为{len(frame)}行")
    if frame[critical_columns].isna().any().any():
        missing = frame[critical_columns].isna().sum().to_dict()
        raise RuntimeError(f"{location['city']} 气象关键字段存在缺失: {missing}")
    return frame, url


def load_catalog_extensions() -> tuple[list[dict], list[dict]]:
    sources: list[dict] = []
    facts: list[dict] = []
    if not EXTENSIONS.exists():
        return sources, facts
    required_source_fields = {
        "source_id", "scenario", "category", "publisher", "title", "url",
        "data_year", "authority", "use_in_model", "caveat",
    }
    required_fact_fields = {
        "source_id", "scenario", "indicator", "value", "unit",
        "model_mapping", "value_type",
    }
    for path in sorted(EXTENSIONS.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw_row in payload.get("sources", []):
            row = dict(raw_row)
            # Accept the compact research schema used by some specialist
            # collectors and normalise it into the public catalog contract.
            if "source_id" not in row and "id" in row:
                row = {
                    "source_id": row["id"],
                    "scenario": "scenario3_high_quality_newbuild",
                    "category": row.get("authority_type", "专项现实校准"),
                    "publisher": row.get("institution", ""),
                    "title": row.get("title", ""),
                    "url": row.get("url", ""),
                    "data_year": str(row.get("year", "")),
                    "authority": row.get("authority_type", ""),
                    "use_in_model": "详见同一来源的结构化事实与model_mapping字段",
                    "caveat": row.get("limitations", ""),
                }
            missing = required_source_fields - set(row)
            if missing:
                raise RuntimeError(f"{path.name} 来源记录缺少字段: {sorted(missing)}")
            sources.append(dict(row))
        for raw_row in payload.get("facts", []):
            row = dict(raw_row)
            if "indicator" not in row and "metric" in row:
                value = row.get("value")
                if isinstance(value, (list, dict)):
                    value = json.dumps(value, ensure_ascii=False)
                row = {
                    "source_id": row["source_id"],
                    "scenario": "scenario3_high_quality_newbuild",
                    "indicator": row["metric"],
                    "value": value,
                    "unit": row.get("unit", ""),
                    "model_mapping": row.get("model_field", ""),
                    "value_type": row.get("topic", "专项量化事实"),
                }
            missing = required_fact_fields - set(row)
            if missing:
                raise RuntimeError(f"{path.name} 事实记录缺少字段: {sorted(missing)}")
            facts.append(dict(row))
    return sources, facts


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取并整理三类供热场景公开数据")
    parser.add_argument(
        "--skip-weather",
        action="store_true",
        help="仅重建来源目录和量化事实，保留已有NASA逐时气象文件",
    )
    parser.add_argument(
        "--weather-years",
        nargs="+",
        type=int,
        default=[DEFAULT_YEAR],
        help="需要抓取的公历年，可一次指定多个年份（默认2023）",
    )
    parser.add_argument(
        "--reuse-existing-weather",
        action="store_true",
        help="文件已存在且行数有效时复用，避免重复请求NASA接口",
    )
    args = parser.parse_args()
    weather_years = sorted(set(args.weather_years))
    if any(year < 2000 for year in weather_years):
        parser.error("NASA POWER逐小时原型年份不得早于2000")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    extension_sources, extension_facts = load_catalog_extensions()
    all_sources = [*SOURCES, *extension_sources]
    source_ids = [str(row["source_id"]) for row in all_sources]
    duplicates = sorted({source_id for source_id in source_ids if source_ids.count(source_id) > 1})
    if duplicates:
        raise RuntimeError(f"公开来源 source_id 重复: {duplicates}")
    source_rows = [{**row, "retrieved_at_utc": retrieved_at} for row in all_sources]
    source_frame = pd.DataFrame(source_rows)
    source_frame.to_csv(OUTPUT / "source_catalog.csv", index=False, encoding="utf-8-sig")

    fact_columns = [
        "source_id", "scenario", "indicator", "value", "unit",
        "model_mapping", "value_type",
    ]
    base_fact_frame = pd.DataFrame(FACTS, columns=fact_columns)
    extension_fact_frame = pd.DataFrame(extension_facts, columns=fact_columns)
    fact_frame = pd.concat([base_fact_frame, extension_fact_frame], ignore_index=True)
    unknown_sources = set(fact_frame["source_id"]) - set(source_frame["source_id"])
    if unknown_sources:
        raise RuntimeError(f"结构化事实引用了未知来源: {sorted(unknown_sources)}")
    fact_frame.to_csv(OUTPUT / "scenario_public_facts.csv", index=False, encoding="utf-8-sig")

    if args.skip_weather:
        print(json.dumps({
            "retrieved_at_utc": retrieved_at,
            "source_count": int(len(source_frame)),
            "fact_count": int(len(fact_frame)),
            "extension_files": [path.name for path in sorted(EXTENSIONS.glob("*.yaml"))]
            if EXTENSIONS.exists() else [],
            "weather_refresh_skipped": True,
        }, ensure_ascii=False, indent=2))
        return

    weather_manifest = {
        "years": weather_years,
        "time_standard": "local_solar_time",
        "profiles": [],
    }
    metadata: dict[str, object] = {
        "retrieved_at_utc": retrieved_at,
        "weather_provider": "NASA POWER Hourly API",
        "weather_profiles": {},
    }
    for year in weather_years:
        for name, location in LOCATIONS.items():
            filename = f"{name}_weather_{year}.csv"
            existing_path = OUTPUT / filename
            expected_rows = 8784 if calendar.isleap(year) else 8760
            if args.reuse_existing_weather and existing_path.exists():
                frame = pd.read_csv(existing_path)
                if len(frame) != expected_rows:
                    raise RuntimeError(
                        f"{location['city']} {year}年既有气象文件应为"
                        f"{expected_rows}行，实际为{len(frame)}行"
                    )
                request_url = f"reused_existing_file:{filename}"
            else:
                frame, request_url = fetch_power(location, year)
            frame.to_csv(
                existing_path,
                index=False,
                encoding="utf-8-sig",
                quoting=csv.QUOTE_MINIMAL,
            )
            weather_manifest["profiles"].append({
                **location,
                "year": year,
                "file": filename,
                "rows": int(len(frame)),
                "source_id": "COMMON_NASA_POWER",
            })
            metadata["weather_profiles"][f"{name}_{year}"] = {
                "year": year,
                "rows": int(len(frame)),
                "missing_values": {
                    key: int(value) for key, value in frame.isna().sum().items()
                },
                "request_url": request_url,
                "temperature_min_c": float(frame["outdoor_temp"].min()),
                "temperature_max_c": float(frame["outdoor_temp"].max()),
                "relative_humidity_mean_pct": float(
                    frame["relative_humidity_pct"].mean()
                ),
            }

    (OUTPUT / "weather_manifest.yaml").write_text(
        yaml.safe_dump(weather_manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (OUTPUT / "crawl_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
