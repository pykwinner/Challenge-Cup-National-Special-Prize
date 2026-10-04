def exact_rc_coefficients(
    heat_loss_kw_per_c: float, thermal_capacity_kwh_per_c: float, dt_hours: float,
) -> tuple[float, float]:
    """Return a, b for T_end = a*T_start + (1-a)*Tout + b*(heat-cool+gains).

    expm1 avoids cancellation at small H*dt/C. H=0 is deliberately rejected:
    this module requires a positive envelope/ventilation heat loss coefficient.
    """
    h = _number(heat_loss_kw_per_c, "heat_loss_kw_per_c", positive=True)
    c = _number(thermal_capacity_kwh_per_c, "thermal_capacity_kwh_per_c", positive=True)
    dt = _number(dt_hours, "dt_hours", positive=True)
    decay = h * (dt / c)
    a, b = exp(-decay), -expm1(-decay) / h
    if not isfinite(b) or b <= 1e-12:
        raise ValueError("RC coefficient b is outside LinearMILP numerical resolution; rescale units")
    return a, b
