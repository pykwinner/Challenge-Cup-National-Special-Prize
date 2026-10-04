可编辑SVG伴随文件（2026-10-04）

这三份SVG是本轮image_gen数据图的精确数据编辑伴随文件。论文采用根任务选定的image_gen PNG；本目录不会替代PNG，不修改PNG或TeX，不新增模型实验。

文件：
- bdg2_data_a3.svg：六行频数／软校准／硬权重，18个等径数据圆（r=48），颜色按归一W1百分数分箱：<5、5—<10、10—<15、15—<20、≥20。仅完整日域的同年描述性压缩对照，统计拒绝保留。
- thermal_factorial_a3.svg：列为原热容／四倍热容，行为无／有公用电上限。四个数据圆等径（r=83），圆内数字为峰值kW，颜色仅映射2880小时能源费；费用与电量文字另列。
- energy_balance_a3.svg：285.9832=191.5475+94.4357 kWh热量分配，流宽按未舍入热量比例；泵电0.1901kWh单独列，不加到热分配中。最大压降237.96对300Pa/m上限、最低远端供温57.45对55℃下限。为72小时有限稳态模式参考网络，非现场/真实地理管网。

数据唯一来源：review/figures_a3_20261004/data_evidence.json中的E01/E03/E04保存值；数值在SVG metadata和data-*属性保留未舍入原值，图面按声明精度展示。

可在Inkscape、Illustrator或浏览器中打开。文字仍为原生text元素，圆、线、矩形及流带路径均有独立id并可独立编辑；没有嵌入PNG、文字转曲或不可分离的截图。中文字体按Microsoft YaHei/PingFang SC/Noto Sans CJK SC/SimHei回退；接收端若缺字体，可安装对应字体或自行换字体后检查换行。

验证：XML解析通过、元素id唯一、无image栅格元素；18个BDG2数据值逐项匹配源JSON且等径；四个峰值和费用逐项匹配且等径；热量分配、泵电及温压边界数值核对通过。未运行优化器。

bdg2_data_a3.svg SHA-256: 38b00c70d48c08fb5a273e9a56cd12f756c154092b331f113af0f5df313f9ff8
thermal_factorial_a3.svg SHA-256: 7d2336be517283e49d6677556522fcf1b7cb90853b415299019ae3edb83c2f8e
energy_balance_a3.svg SHA-256: 6b88bde4350a73fd601419a9cfd77a18682388c60c7a2205affdfb1347b95c09
