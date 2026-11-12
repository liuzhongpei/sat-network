"""Walker 星座圆轨道递推与星间几何计算.

坐标系: 地心惯性系(ECI), 圆轨道近似(LEO 星座 ISL 计算精度足够).
"""
import math

MU_EARTH = 3.986004418e14      # 地球引力常数 (m^3/s^2)
EARTH_RADIUS_KM = 6371.0       # 地球平均半径 (km)
LIGHT_SPEED_KM_S = 299792.458  # 光速 (km/s)
EARTH_ROT_RAD_S = 7.2921159e-5  # 地球自转角速度 (rad/s)


class Constellation:
    """Walker 星座: planes x sats_per_plane, 圆轨道."""

    def __init__(self, planes, sats_per_plane, altitude_km,
                 inclination_deg, raan_spread_deg=360.0,
                 phase_factor=1, epoch_offset_s=0.0):
        self.planes = int(planes)
        self.sats_per_plane = int(sats_per_plane)
        self.altitude_km = float(altitude_km)
        self.inclination = math.radians(float(inclination_deg))
        self.raan_spread = math.radians(float(raan_spread_deg))
        self.phase_factor = int(phase_factor)
        self.epoch_offset_s = float(epoch_offset_s)
        self.radius_km = EARTH_RADIUS_KM + self.altitude_km
        # 轨道周期 T = 2*pi*sqrt(a^3/mu)
        a_m = self.radius_km * 1000.0
        self.period_s = 2.0 * math.pi * math.sqrt(a_m ** 3 / MU_EARTH)
        self.total_sats = self.planes * self.sats_per_plane

    def position(self, plane, sat, t_s):
        """卫星 (plane, sat) 在 t_s 时刻的 ECI 位置 (km).

        返回 (x, y, z).
        """
        t = t_s + self.epoch_offset_s
        raan = self.raan_spread * plane / self.planes
        # 纬度幅角 u = 面内相位 + Walker 相位偏移 + 平运动
        u = (2.0 * math.pi * sat / self.sats_per_plane
             + 2.0 * math.pi * self.phase_factor * plane
             / (self.planes * self.sats_per_plane)
             + 2.0 * math.pi * t / self.period_s)
        r = self.radius_km
        cos_u, sin_u = math.cos(u), math.sin(u)
        cos_i, sin_i = math.cos(self.inclination), math.sin(self.inclination)
        cos_o, sin_o = math.cos(raan), math.sin(raan)
        x = r * (cos_o * cos_u - sin_o * sin_u * cos_i)
        y = r * (sin_o * cos_u + cos_o * sin_u * cos_i)
        z = r * (sin_u * sin_i)
        return (x, y, z)

    def latitude_deg(self, plane, sat, t_s):
        """卫星星下点地心纬度 (deg)."""
        _, _, z = self.position(plane, sat, t_s)
        return math.degrees(math.asin(max(-1.0, min(1.0, z / self.radius_km))))

    def subpoint_deg(self, plane, sat, t_s):
        """星下点 (纬度, 经度) deg, 经度含地球自转修正 (可视化用)."""
        x, y, z = self.position(plane, sat, t_s)
        lat = math.degrees(
            math.asin(max(-1.0, min(1.0, z / self.radius_km))))
        lon = math.degrees(math.atan2(y, x)) - math.degrees(
            EARTH_ROT_RAD_S * (t_s + self.epoch_offset_s))
        lon = (lon + 180.0) % 360.0 - 180.0
        return lat, lon

    def distance_km(self, a, b, t_s):
        """两颗卫星 a=(p,s), b=(p,s) 之间的直线距离 (km)."""
        xa = self.position(a[0], a[1], t_s)
        xb = self.position(b[0], b[1], t_s)
        return math.dist(xa, xb)

    def propagation_delay_ms(self, a, b, t_s):
        """星间链路传播时延 (ms)."""
        return self.distance_km(a, b, t_s) / LIGHT_SPEED_KM_S * 1000.0

    def line_of_sight(self, a, b, t_s, margin_km=100.0):
        """星间可见性: 连线与地心最近距离是否高于地表+余量."""
        xa = self.position(a[0], a[1], t_s)
        xb = self.position(b[0], b[1], t_s)
        ab = tuple(xb[i] - xa[i] for i in range(3))
        ab2 = sum(c * c for c in ab)
        if ab2 == 0.0:
            return True
        # 地心 O 到线段 ab 的最近点参数
        tproj = -sum(xa[i] * ab[i] for i in range(3)) / ab2
        tproj = max(0.0, min(1.0, tproj))
        closest = tuple(xa[i] + tproj * ab[i] for i in range(3))
        return math.dist((0.0, 0.0, 0.0), closest) > EARTH_RADIUS_KM + margin_km


def make_constellation(cfg):
    """从配置字典 (constellation 段) 构造 Constellation."""
    return Constellation(
        planes=cfg["planes"],
        sats_per_plane=cfg["sats_per_plane"],
        altitude_km=cfg["altitude_km"],
        inclination_deg=cfg["inclination_deg"],
        raan_spread_deg=cfg.get("raan_spread_deg", 360.0),
        phase_factor=cfg.get("phase_factor", 1),
        epoch_offset_s=cfg.get("epoch_offset_s", 0.0),
    )
