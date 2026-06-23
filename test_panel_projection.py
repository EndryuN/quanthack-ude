"""Forward projection cone: slope by direction*confidence, width by vol."""
from panel.backend.projection import projection_cone

p = 100.0
up = projection_cone(p, "long", 0.5, vol=0.01, horizon_bars=8)
assert up["mid"][0] == [0, 100.0], "starts at current price"
assert up["mid"][-1][1] > p, "long slopes up"
assert up["upper"][-1][1] > up["mid"][-1][1] > up["lower"][-1][1], "band brackets mid"

dn = projection_cone(p, "short", 0.5, vol=0.01)
assert dn["mid"][-1][1] < p, "short slopes down"

flat = projection_cone(p, "flat", 0.0, vol=0.01)
assert flat["mid"][-1][1] == p, "flat stays at price"
assert flat["upper"][-1][1] > p, "flat still has a vol band"

# higher confidence -> steeper slope
strong = projection_cone(p, "long", 0.9, vol=0.01)
assert strong["mid"][-1][1] > up["mid"][-1][1]
# higher vol -> wider band
wide = projection_cone(p, "long", 0.5, vol=0.05)
assert (wide["upper"][-1][1] - wide["lower"][-1][1]) > (up["upper"][-1][1] - up["lower"][-1][1])
print("OK")
