# C7 最终研究论文交接

更新：2026-10-02。**约定的研究论文交付已完成，当前源/PDF已冻结；工程参数来源仍未闭合，不能作为可正式提交的工程实证论文。**

## 最终路径和依赖

- 主文件：`D:\competion\xiaotiao\16\16_LaTeX源文件\main.tex`
- 最终PDF：`D:\competion\xiaotiao\16\C7_compact_method_build_20261001\C7_research_paper_20261002.pdf`
- 引用源：`D:\competion\xiaotiao\16\16_LaTeX源文件\sections\model-derivation.tex`、`D:\competion\xiaotiao\16\16_LaTeX源文件\sections\algorithm-revision.tex`、`D:\competion\xiaotiao\16\16_LaTeX源文件\sections\model-appendix.tex`
- 复制依赖：`D:\competion\xiaotiao\16\C7_edit_records_20261001\C7_paper_dependency_manifest.json` / 同名MD。
- 最终验收：`D:\competion\xiaotiao\16\C7_edit_records_20261001\C7_final_validation.json`；全文：`D:\competion\xiaotiao\16\C7_edit_records_20261001\C7_pdf_page_text.txt`。

保持9个文件相对路径：4 tex、3 build-fonts字体、源根目录cover.jpg/body-background.jpg。recorder确认6个文本/图片输入，3字体由fontspec路径声明核对。图片不是media子目录。两幅图均为原生TikZ，无新增位图依赖；无需复制旧结果图或未引用algorithm-framework.tex。C0可按清单复制到deliverables/paper，工程代码仍在工程，不嵌入论文。

## 编译、字数与视觉

19页；提取汉字8156（目录、表、两幅图、图题、9项参考资料、附录均计入），封面及18页背景图文字补178，总8334，距9500目标余1166。额外非空白提取字符12541、英文词464另记。内部汉字口径不冒称竞赛官方算法。

最后连续两遍指定XeLaTeX均exit=0，2.992s/2.926s。错误/未定义引用/缺图/字体与缺字/Overfull/包警告均0，仅2条Underfull。19页全渲染并检查四张联系页，物理页14模型/五行表/原生前沿图放大通过；数据来源、N2完整对照、文献和伪代码先前重点检查保持有效。C0独立确认19页并渲染13/14/17页，未发现裁切、可读性或范围问题。

PDF SHA256：`cb0feba158e0e3240e1e5cfdcc30f61a2a5b131c88cecdb50f8380d427bee335`。源SHA和9依赖SHA见清单；原前导区、字体、封面背景、input目标及附录显式label保留。原16.pdf与未引用algorithm-framework.tex哈希不变。

## 三类获准结果已写入

1. NASA：C0_weather_02正式复核15份三城市2020–2024资料，131544h×4=526176值与本次响应一致。C7核对报告manifest及15份CSV/响应SHA。正文记API v2.10.2、辐射Wh/m²、LST太阳时和2023重建校验查询的历史缺口，只认证四个产品字段，不认证建筑/生成负荷/价格/地面站精度。
2. N2：首轮与后续探索分别获C0批准，manifest/C6审查/C0完整重拟合SHA核对通过。物理页13同表三模式，首轮/后续/基准为一步0.085979/0.091334/0.091964、连续开环1.885259/7.425830/0.790172、分段开环1.155136/6.170260/0.626294。记录11988条及7192/2397/2399连续划分。后续同划分探索不视为新独立验证，未隐瞒恶化；处理/聚合窗口、源补值无行旗及可能跨划分信息、太阳代理、内部方案非独立预注册、德国H/C不迁移等限定均保留，注明来源/CC BY-SA4.0/派生处理。
3. Mavrotas：C0正式批准公开数学复现。C7核对manifest SHA `5d9352ebadfc19482decaf1f0757c60e39f46c1171b7d9e750d57c67d33e5c9e` 及106文件SHA。物理页14列原问题、两端点和五个实际分层ε点；原生图按获准坐标绘制。23次LP、22最优/1预期不可行，0矩阵/变量界违反和重算误差，累计0.017743s仅指小例题。准确标为LinearMILP数值层+独立两阶段包装，不是HeatPlanningModel.solve_lexicographic、完整AUGMECON或供热工程收益。628项回归通过/67.55s仅为软件记录；C7读取XML核对，未重跑测试。

## 文献与范围

9项参考资料全部在相应正文引用，编号及label/ref核对通过。Bacher/Madsen、IEA Annex58、Kotzur、Mavrotas、SciPy1.15.3、HiGHS开发版文档、Fraunhofer及NASA均按真实原始出处和版本记录。实际阅读范围见C7_reference_verification.md/json，不冒称全文阅读。Annex58按2016出版版权页引用，记录2015年12月编写日期。方法来源不认证武汉H/C、电价、空间或设备报价。

专用experiments.py扫描碳价置零；run_competition_frontier.py继承情景总目标，保留碳成本及启用偏好项。正文未把后者称为无碳价纯成本前沿，有限采样也不称完整精确Pareto。

未经来源准入的武汉原型容量、费用和改善率未写成工程成果。经济碳保留公式及台账口径，真实参数/报价/公平基准不足时不编收益。旧三场景、4392/2880h、虚拟RC、旧改善率均保留历史身份，不冒充本轮成果。C3指明武汉草案ACOP不能认证当前EER，本文未作该推断。未知企业认可/鉴定/成员经历不补写。

## 用户PPT补充和历史

用户PPT顺序与“来源真实可查、不得伪造数据”已补入C7_user_constraints.md：选题→九人实际分工→7月简版/8月精版/10月一版→主要内容→企业联合完善→真实认可/鉴定→展望不足。未知事实留白；末句“再加一条”具体内容待用户补充。未改C5逐页MD/PPT。

原始基线和阶段A完整源在.algorithm_doc_work/execution_20261001/C7_baseline（phaseA_edited）；旧49页17056汉字保真稿保留但不是最终交付。当前研究稿采用新PDF文件名，旧待来源稿/原16.pdf均保留。历史作者脚本有原状态断言，不得对现稿直接重放。

关键新增记录：C7_external_evidence_admitted.json、C7_external_followup_admitted.json、C7_published_method_admitted.json、C7_final_validation.json、C7_paper_dependency_manifest.json。没有待批准的本轮正文结果，不再修改冻结源/PDF。工程实证参数闭合另需真实资料。未修改模型、测试、C5，未创建代理或向其他聊天发送消息；保持用户指定模型和普通速度，未开展新研究。
