# 逐楼热状态是独立RC分支，节点平衡须消费其交付量。
def add_building_thermal_constraints(model: Any) -> BuildingThermalBlock | None:
    """Register on model.problem / model.variables and return load variable maps.

    Needs config, ts, dt, problem and variables, optionally boundary_groups.
    Adds no source equipment, costs, unmet-load variables or reliability credits.
    The caller MUST consume node_heat/node_cooling in physical supply balances.
    """
    # 先校验逐楼输入与分组边界，未启用则返回空。
    inputs = validate_building_thermal_inputs(model.config, model.ts, dt_hours=model.dt,
                                             boundary_groups=getattr(model, "boundary_groups", None))
    if inputs is None:
        return None
    categories = ("building_temperature", "building_heat", "building_cooling", "building_heat_mode",
                  "building_heat_by_node", "building_cooling_by_node")
    # 拒绝重复注册，避免同一建筑出现两套状态变量。
    if any(model.variables.get(category) for category in categories):
        raise ValueError("building thermal variables already registered")
    maps = {category: {} for category in categories}
    temperature, heat, cooling, mode, node_heat, node_cooling = (maps[category] for category in categories)
    p = model.problem
    heat_members: dict[tuple[str, str, int], list[int]] = defaultdict(list)
    cooling_members: dict[tuple[str, int], list[int]] = defaultdict(list)
    for building in inputs.buildings:
        for t in range(len(inputs.frame)):
            key = building.name, t
            # 室温单位为摄氏度，逐时舒适区间直接作为变量界。
            temperature[key] = p.add_var(f"building_temperature[{building.name},{t}]", lb=building.minimum[t], ub=building.maximum[t])
            # 冷热交付均为非负kW，各自受末端功率上界限制。
            heat[key] = p.add_var(f"building_heat[{building.name},{t}]", ub=building.heat_upper[t])
            cooling[key] = p.add_var(f"building_cooling[{building.name},{t}]", ub=building.cooling_upper[t])
            # 按建筑所属节点和供热温位收集实际交付功率。
            heat_members[building.node, building.heat_grade, t].append(heat[key])
            cooling_members[building.node, t].append(cooling[key])
            # 仅互斥模式且冷热均可用时引入二元开关。
            if inputs.simultaneous_mode == "exclusive" and building.heat_upper[t] > 0 and building.cooling_upper[t] > 0:
                mode[key] = p.add_var(f"building_heat_mode[{building.name},{t}]", ub=1.0, integer=True)
                # 模式为1允许供热；模式为0允许制冷。
                p.add_constraint({heat[key]: 1.0, mode[key]: -building.heat_upper[t]}, ub=0.0)
                p.add_constraint({cooling[key]: 1.0, mode[key]: building.cooling_upper[t]}, ub=building.cooling_upper[t])
        # 状态递推只连接所属边界组，避免跨场景串联。
        for group, times in inputs.boundary_groups.items():
            condition = building.boundary[group]
            for position, t in enumerate(times):
                key = building.name, t
                # a无量纲，b为K每kW；供热升温而制冷降温。
                row = {temperature[key]: 1.0, heat[key]: -building.b, cooling[key]: building.b}
                # H乘室外温度与内、太阳得热合成递推常量项。
                rhs = building.b * (building.heat_loss_kw_per_c * inputs.outdoor_temperature[t]
                                     + building.internal_gain[t] + building.solar_gain[t])
                # 固定组首时段把初始室温乘a移到等式右端。
                if position == 0 and condition.mode == "fixed":
                    rhs += building.a * condition.initial_temperature_c
                else:
                    previous = temperature[building.name, times[position - 1]]
                    # Addition is essential for single-step cyclic groups (same index).
                    # 循环组接组内前驱，单时段同索引须累加系数。
                    row[previous] = row.get(previous, 0.0) - building.a
                p.add_constraint(row, lb=rhs, ub=rhs)
            # 固定边界另约束末态室温，不强制循环相等。
            if condition.mode == "fixed":
                p.add_constraint({temperature[building.name, times[-1]]: 1.0},
                                 lb=condition.final_min_temperature_c,
                                 ub=condition.final_max_temperature_c if condition.final_max_temperature_c is not None else np.inf)
    # 多楼以等式汇总交付量，单楼直接复用变量索引。
    for category, members, target in (("building_heat_by_node", heat_members, node_heat),
                                      ("building_cooling_by_node", cooling_members, node_cooling)):
        for key, indices in members.items():
            if len(indices) == 1:
                target[key] = indices[0]  # Alias avoids a redundant row/variable.
            else:
                aggregate = p.add_var(f"{category}[{','.join(map(str, key))}]", ub=sum(p.upper[i] for i in indices))
                target[key] = aggregate
                row = {index: -1.0 for index in indices}
                row[aggregate] = 1.0
                p.add_constraint(row, lb=0.0, ub=0.0)
    model.variables.update(maps)
    return BuildingThermalBlock(inputs, temperature, heat, cooling, mode, node_heat, node_cooling)
