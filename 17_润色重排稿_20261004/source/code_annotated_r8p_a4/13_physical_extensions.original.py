def add_shared_heat_resources(model):
    """Bound upstream heat drawn jointly by direct exchangers and heat pumps.

    Q_upstream = Q_direct / eta + Q_HP * (1 - 1/COP) / eta.
    eta is the explicitly declared resource-to-device transfer efficiency.
    Compressor electricity is excluded. Cooling/rejection is not allocated to
    this pool; such sources require a separate, explicitly defined resource model.
    """
    model.shared_heat_resource_rows = []
    names, assigned = set(), set()
    for pool in model.config.get("shared_heat_resources", []):
        name = str(pool["name"])
        members = list(pool["source_names"])
        if not name.strip() or name in names or not members or len(members) != len(set(members)):
            raise ValueError("shared heat resource names/members must be unique and nonempty")
        names.add(name)
        if set(members) - set(model.sources) or assigned.intersection(members):
            raise ValueError(f"resource {name}: unknown source or source assigned to multiple pools")
        assigned.update(members)
        scalar = "available_heat_kw" in pool
        column = "available_heat_column" in pool
        if scalar == column:
            raise ValueError(f"resource {name}: specify exactly one available_heat_kw/column")
        efficiency = _nonnegative(pool["transfer_efficiency"], f"resource {name} efficiency")
        if not 0 < efficiency <= 1:
            raise ValueError("resource transfer_efficiency must be in (0, 1]")
        for source_name in members:
            source = model.sources[source_name]
            if source.get("type") not in {"direct_heat", "heat_pump"}:
                raise ValueError(f"resource {name}: only direct_heat and heat_pump are supported")
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
                    idx = model.variables["source_heat"].get((source_name, grade_name, t))
                    if idx is None:
                        continue
                    fraction = 1.0
                    if source.get("type") == "heat_pump":
                        cop = float(model._cop(source, grade, t))
                        if not isfinite(cop) or cop <= 1:
                            raise ValueError("a resource heat pump requires finite COP > 1")
                        fraction -= 1.0 / cop
                    model._add(terms, idx, fraction / efficiency)
            use = model.problem.add_var(f"shared_heat_resource_use[{name},{t}]", ub=available)
            model.variables["shared_heat_resource_use"][(name, t)] = use
            model.problem.add_constraint({use: 1.0, **{i: -c for i, c in terms.items()}}, lb=0.0, ub=0.0)
            model.shared_heat_resource_rows.append((name, t, available, terms, use))
