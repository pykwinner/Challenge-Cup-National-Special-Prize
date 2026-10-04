def add_building_thermal_constraints(model: Any) -> BuildingThermalBlock | None:
    """Register on model.problem / model.variables and return load variable maps.

    Needs config, ts, dt, problem and variables, optionally boundary_groups.
    Adds no source equipment, costs, unmet-load variables or reliability credits.
    The caller MUST consume node_heat/node_cooling in physical supply balances.
    """
    inputs = validate_building_thermal_inputs(model.config, model.ts, dt_hours=model.dt,
                                             boundary_groups=getattr(model, "boundary_groups", None))
    if inputs is None:
        return None
    categories = ("building_temperature", "building_heat", "building_cooling", "building_heat_mode",
                  "building_heat_by_node", "building_cooling_by_node")
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
            temperature[key] = p.add_var(f"building_temperature[{building.name},{t}]", lb=building.minimum[t], ub=building.maximum[t])
            heat[key] = p.add_var(f"building_heat[{building.name},{t}]", ub=building.heat_upper[t])
            cooling[key] = p.add_var(f"building_cooling[{building.name},{t}]", ub=building.cooling_upper[t])
            heat_members[building.node, building.heat_grade, t].append(heat[key])
            cooling_members[building.node, t].append(cooling[key])
            if inputs.simultaneous_mode == "exclusive" and building.heat_upper[t] > 0 and building.cooling_upper[t] > 0:
                mode[key] = p.add_var(f"building_heat_mode[{building.name},{t}]", ub=1.0, integer=True)
                p.add_constraint({heat[key]: 1.0, mode[key]: -building.heat_upper[t]}, ub=0.0)
                p.add_constraint({cooling[key]: 1.0, mode[key]: building.cooling_upper[t]}, ub=building.cooling_upper[t])
        for group, times in inputs.boundary_groups.items():
            condition = building.boundary[group]
            for position, t in enumerate(times):
                key = building.name, t
                row = {temperature[key]: 1.0, heat[key]: -building.b, cooling[key]: building.b}
                rhs = building.b * (building.heat_loss_kw_per_c * inputs.outdoor_temperature[t]
                                     + building.internal_gain[t] + building.solar_gain[t])
                if position == 0 and condition.mode == "fixed":
                    rhs += building.a * condition.initial_temperature_c
                else:
                    previous = temperature[building.name, times[position - 1]]
                    # Addition is essential for single-step cyclic groups (same index).
                    row[previous] = row.get(previous, 0.0) - building.a
                p.add_constraint(row, lb=rhs, ub=rhs)
            if condition.mode == "fixed":
                p.add_constraint({temperature[building.name, times[-1]]: 1.0},
                                 lb=condition.final_min_temperature_c,
                                 ub=condition.final_max_temperature_c if condition.final_max_temperature_c is not None else np.inf)
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
