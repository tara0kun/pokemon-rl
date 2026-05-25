from pokemon_env import PokemonEmeraldEnv
import time

env = PokemonEmeraldEnv()

print("環境をリセット...")
obs, info = env.reset()
print(f"観測の形状: {obs.shape}")  # (84, 84, 1) と表示されればOK

print("ランダムに10回行動させます...")
for i in range(10):
    action = env.action_space.sample()  # ランダムな行動
    obs, reward, terminated, truncated, info = env.step(action)
    print(f"ステップ{i+1}: 行動={env.BUTTONS[action]}, 報酬={reward}")
    time.sleep(0.5)

print("テスト完了！")
env.close()
