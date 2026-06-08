import pyautogui
from PIL import ImageGrab
import pygetwindow as gw

def get_mgba_screenshot():
    windows = gw.getWindowsWithTitle("mGBA")
    
    if not windows:
        print("mGBAウィンドウが見つかりません")
        return None
    
    win = windows[0]
    
    # オフセットを調整
    left = win.left + 8    # 左の黒帯を除外
    top = win.top + 48     # タイトルバー+メニューバーを除外
    right = win.right - 8  # 右の黒帯を除外
    bottom = win.bottom - 8 # 下の黒帯を除外
    
    img = ImageGrab.grab(bbox=(left, top, right, bottom))
    img.save("screenshot_mgba.png")
    print("保存しました！")
    return img

get_mgba_screenshot()
