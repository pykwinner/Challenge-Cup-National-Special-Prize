# 本函数属于独立1R1C分支，不替换历史聚合需求响应状态。
def exact_rc_coefficients(
    heat_loss_kw_per_c: float, thermal_capacity_kwh_per_c: float, dt_hours: float,
) -> tuple[float, float]:
    """Return a, b for T_end = a*T_start + (1-a)*Tout + b*(heat-cool+gains).

    expm1 avoids cancellation at small H*dt/C. H=0 is deliberately rejected:
    this module requires a positive envelope/ventilation heat loss coefficient.
    """
    # H、C与步长必须为正且有限，单位应先统一。
    h = _number(heat_loss_kw_per_c, "heat_loss_kw_per_c", positive=True)
    c = _number(thermal_capacity_kwh_per_c, "thermal_capacity_kwh_per_c", positive=True)
    dt = _number(dt_hours, "dt_hours", positive=True)
    # H乘步长除以C为无量纲指数，避免将秒与小时混算。
    decay = h * (dt / c)
    # expm1减小小指数下的数值相消；b乘净输入功率得到温升。
    a, b = exp(-decay), -expm1(-decay) / h
    # 系数低于矩阵数值分辨能力时明确报错，不返回无效热状态。
    if not isfinite(b) or b <= 1e-12:
        raise ValueError("RC coefficient b is outside LinearMILP numerical resolution; rescale units")
    return a, b
