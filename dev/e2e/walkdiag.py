exec(open(__file__.replace("walkdiag.py", "widetest.py")).read().split("def walk(app")[0])
import numpy as np
black = 0; frames = 0
for d in ("right", "right", "up", "up", "left", "down", "down", "left"):
    A.press(d, True)
    for _ in range(16):
        A.step(); B.step(); A.render(); frames += 1
        margin = pygame.surfarray.array3d(A.game_surf)[:60, :, :]   # left margin strip
        if margin.max() < 20: black += 1
    A.press(d, False)
    for _ in range(2): A.step(); B.step(); A.render(); frames += 1
print(f"frames {frames}, frames with black margins: {black}")
A.shutdown(); B.shutdown(); srv.terminate()
