# 本函数使用已注册热源出力，不新增热源设备。
def add_shared_heat_resources(model):
    """Bound upstream heat drawn jointly by direct exchangers and heat pumps.

    Q_upstream = Q_direct / eta + Q_HP * (1 - 1/COP) / eta.
    eta is the explicitly declared resource-to-device transfer efficiency.
    Compressor electricity is excluded. Cooling/rejection is not allocated to
    this pool; such sources require a separate, explicitly defined resource model.
    """
    # 资源行保留可用上界、出力系数与取热变量。
    model.shared_heat_resource_rows = []
    names, assigned = set(), set()
    # 只有配置登记的资源池才会增加这组约束。
    for pool in model.config.get("shared_heat_resources", []):
        name = str(pool["name"])
        members = list(pool["source_names"])
        # 资源名称和成员须唯一，避免资源账本重复。
        if not name.strip() or name in names or not members or len(members) != len(set(members)):
            raise ValueError("shared heat resource names/members must be unique and nonempty")
        names.add(name)
        # 同一热源不可分属多个资源池而重复计量。
        if set(members) - set(model.sources) or assigned.intersection(members):
            raise ValueError(f"resource {name}: unknown source or source assigned to multiple pools")
        assigned.update(members)
        # 可用热功率以kW登记，常量与时序二选一。
        scalar = "available_heat_kw" in pool
        column = "available_heat_column" in pool
        if scalar == column:
            raise ValueError(f"resource {name}: specify exactly one available_heat_kw/column")
        # 效率无量纲，输出热量折回上游时需除以它。
        efficiency = _nonnegative(pool["transfer_efficiency"], f"resource {name} efficiency")
        if not 0 < efficiency <= 1:
            raise ValueError("resource transfer_efficiency must be in (0, 1]")
        for source_name in members:
            source = model.sources[source_name]
            # 此资源口径仅接受直供换热器与供热热泵。
            if source.get("type") not in {"direct_heat", "heat_pump"}:
                raise ValueError(f"resource {name}: only direct_heat and heat_pump are supported")
            # 制冷或排热设备须另建资源守恒，不能套用。
            if any(key[0] == source_name for key in model.variables["cooling_output"]) or source.get("heat_rejection_enabled"):
                raise ValueError(f"resource {name}: cooling/rejection needs a separate resource balance")
        for t in model.times:
            available = _nonnegative(
                model._ts_value(t, pool["available_heat_column"]) if column else pool["available_heat_kw"],
                f"resource {name} available heat at {t}",
            )
            terms = {}
            for source_name in members:
                source = model.sources[source_name]
                for grade_name, grade in model.grades.items():
                    # 按温位汇总同一成员已有的供热功率变量。
                    idx = model.variables["source_heat"].get((source_name, grade_name, t))
                    if idx is None:
                        continue
                    # 直供取热比例为1；热泵需扣除压缩机电功。
                    fraction = 1.0
                    if source.get("type") == "heat_pump":
                        cop = float(model._cop(source, grade, t))
                        if not isfinite(cop) or cop <= 1:
                            raise ValueError("a resource heat pump requires finite COP > 1")
                        # 热泵上游取热为供热功率乘以1减COP倒数。
                        fraction -= 1.0 / cop
                    # 再除传热效率，得到每kW供热对应的上游取热。
                    model._add(terms, idx, fraction / efficiency)
            # 取热变量上界为逐时可用资源，等式绑定总取热。
            use = model.problem.add_var(f"shared_heat_resource_use[{name},{t}]", ub=available)
            model.variables["shared_heat_resource_use"][(name, t)] = use
            model.problem.add_constraint({use: 1.0, **{i: -c for i, c in terms.items()}}, lb=0.0, ub=0.0)
            model.shared_heat_resource_rows.append((name, t, available, terms, use))
