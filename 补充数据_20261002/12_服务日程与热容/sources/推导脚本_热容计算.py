# -*- coding: utf-8 -*-
"""单位面积热容推导脚本（12_服务日程与热容 补充数据包）
口径A 全厚：C = Σ(ρ_i·c_i·d_i)，kJ/(m2·K)，按构件面积
口径B ISO有效厚度：只计内侧至第一层保温材料之间，单层有效厚度≤0.10 m（ISO 13790:2008 表11 / ISO 13786 简化法，24h周期）
材料参数：GB 50176-2016《民用建筑热工设计规范》表B.1（干密度ρ0、比热容c）
构造做法：假设 + 检索所得典型做法（逐条见CSV说明与README），构造厚度为假设值。
输出：tables/_derived_part.csv（9列，与 thermal_capacity_extracted.csv 同构）
"""
M = {  # 材料 -> (ρ0 kg/m3, c kJ/(kg·K))，取自 GB 50176-2016 表B.1
 '钢筋混凝土':(2500,0.92),'水泥砂浆':(1800,1.05),'重砂浆砌筑粘土砖砌体':(1800,1.05),
 '加气混凝土700':(700,1.05),'夯实粘土':(2000,1.01),'加草粘土1600':(1600,1.01),
 '加草粘土1400':(1400,1.01),'建筑用砂':(1600,1.01)}
URL='https://szelec.cc/zb_users/upload/2023/12/202312181702909360523381.pdf'
def kappa(layers, cap=None):
    return sum(M[n][0]*M[n][1]*(min(d,cap) if cap else d) for n,d in layers)
L = {
 'w370':('乌鲁木齐',[('水泥砂浆',0.02),('重砂浆砌筑粘土砖砌体',0.37),('水泥砂浆',0.02)]),
 'w240':('武汉',[('水泥砂浆',0.02),('重砂浆砌筑粘土砖砌体',0.24),('水泥砂浆',0.02)]),
 'rc200':('武汉',[('水泥砂浆',0.02),('钢筋混凝土',0.20),('水泥砂浆',0.02)]),
 'air200':('通用',[('水泥砂浆',0.02),('加气混凝土700',0.20),('水泥砂浆',0.02)]),
 'adobe400':('固原',[('加草粘土1600',0.02),('加草粘土1600',0.40),('加草粘土1600',0.02)]),
 'hangtu800':('固原',[('夯实粘土',0.80)]),
 'adobe350':('银川参考',[('加草粘土1600',0.35)]),
 'floor':('通用',[('水泥砂浆',0.04),('钢筋混凝土',0.12)]),
 'roof100':('乌鲁木齐',[('钢筋混凝土',0.10)]),
 'mudroof':('固原/银川',[('加草粘土1400',0.20)]),
 'iwbrick120':('通用',[('水泥砂浆',0.02),('重砂浆砌筑粘土砖砌体',0.12),('水泥砂浆',0.02)]),
 'iwair100':('通用',[('水泥砂浆',0.02),('加气混凝土700',0.10),('水泥砂浆',0.02)]),
 'ground':('固原',[('夯实粘土',0.40)]),
}
def desc(layers): return '+'.join(f'{m}{int(d*1000)}mm' for m,d in layers)
CAT={'w370':'外墙构件','w240':'外墙构件','rc200':'外墙构件','air200':'外墙构件',
     'adobe400':'外墙构件','hangtu800':'外墙构件','adobe350':'外墙构件',
     'floor':'楼板构件','roof100':'屋面构件','mudroof':'屋面构件',
     'iwbrick120':'内隔墙构件','iwair100':'内隔墙构件','ground':'地面构件'}
rows=[]
order=['w370','w240','rc200','air200','adobe400','hangtu800','adobe350','floor','roof100','mudroof','iwbrick120','iwair100','ground']
for key in order:
    city,layers=L[key]; d=desc(layers)
    full=kappa(layers); iso=kappa(layers,cap=0.10)
    rows.append(f'{CAT[key]}-{key},{d},面热容(全厚口径),{full:.1f},kJ/(m2·K),推导,{city},GB 50176-2016 表B.1材料参数＋构造假设,{URL}')
    rows.append(f'{CAT[key]}-{key},{d},面热容(ISO有效厚度口径≤100mm),{iso:.1f},kJ/(m2·K),推导,{city},GB 50176-2016 表B.1材料参数＋构造假设（ISO 13790表11有效厚度）,{URL}')
cases=[('乌鲁木齐砖混6层',[('w370',0.40),('iwbrick120',0.50),('floor',1.00),('roof100',0.167)]),
       ('武汉砖混6层',[('w240',0.40),('iwbrick120',0.50),('floor',1.00),('roof100',0.167)]),
       ('武汉剪力墙18层',[('rc200',0.35),('iwair100',0.50),('floor',1.00),('roof100',0.056)]),
       ('固原农宅单层（土坯墙+草泥屋面）',[('adobe400',0.55),('iwbrick120',0.25),('ground',0.30),('mudroof',1.00)])]
for label,items in cases:
    city=label.split('砖')[0].split('剪')[0].split('农')[0]
    ratio='+'.join(f'{k}×{r:.3f}' for k,r in items)
    full=sum(kappa(L[k][1])*r for k,r in items); iso=sum(kappa(L[k][1],cap=0.10)*r for k,r in items)
    rows.append(f'ZONE级示例-{label},"{ratio}",C/Af(全厚口径),{full:.0f},kJ/(m2·K),推导,{city},面积比为假设（外墙0.40/内墙0.50/楼板1.00/屋面分摊等，见README）,{URL}')
    rows.append(f'ZONE级示例-{label},"{ratio}",C/Af(ISO有效口径),{iso:.0f},kJ/(m2·K),推导,{city},面积比为假设（外墙0.40/内墙0.50/楼板1.00/屋面分摊等，见README）,{URL}')
open('tables/_derived_part.csv','w',encoding='utf-8-sig').write('\n'.join(rows)+'\n')
print('rows',len(rows))
