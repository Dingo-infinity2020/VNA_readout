"""A small terminal menu. No network traffic until the user chooses an operation."""
import argparse
import json
from pathlib import Path

import vna


def menu():
    preferences = vna.ROOT / "connection.local.json"
    host = ""
    if preferences.exists():
        try:
            host = json.loads(preferences.read_text()).get("host", "")
        except (ValueError, OSError):
            pass
    print("\nVNA_readout 中文菜单\n自主 U 盘采集：请看 docs/U盘独立采集.md；本菜单是 Mac/PC 调试入口。")
    while True:
        print("\n1 检查连接（只读）  2 单次采集  3 降温监测  4 暗测试\n5 查看/校验已有数据  6 离线演示  7 导入 U 盘  0 退出")
        choice = input("选择：").strip()
        if choice == "0":
            return
        try:
            if choice == "5":
                from report import create_report
                print("离线报告：", create_report(Path(input("实验目录：").strip()).expanduser()))
                continue
            if choice == "6":
                args = argparse.Namespace(command="demo", profile=str(vna.ROOT/"configs/filter.json"),
                                          out="data", kind="kid", count=3)
            elif choice == "7":
                from usb_import import import_usb
                from report import create_report
                source = Path(input("U 盘的 RUN 目录：").strip()).expanduser()
                sample = input("样品名：").strip() or "USB_KID_RUN"
                print("报告：", create_report(import_usb(source, sample=sample)))
                continue
            elif choice in ("1", "2", "3", "4"):
                host = input(f"VNA 当前 IP [{host}]：").strip() or host
                if not host:
                    raise ValueError("需要当前 IP")
                vna.atomic(preferences, vna.json_text({"host": host}))
                if choice == "1":
                    vna.run(argparse.Namespace(command="check", host=host, timeout=5, serial=None))
                    continue
                path = input("配置文件路径 [configs/filter.json，仅用于无源滤波器]：").strip() or str(vna.ROOT/"configs/filter.json")
                profile = vna.load_profile(path)
                print(vna.json_text(profile))
                print("这是电脑调度模式；电脑断联会停止后续采集。未校准数据仍可保存。")
                if input("已核对接线和源功率，输入 START 才开启 RF：").strip() != "START":
                    print("已取消，未连接或修改仪器。")
                    continue
                interval = 60 if choice == "3" else 30
                count = 1
                if choice != "2":
                    interval = vna.positive(input(f"间隔秒数 [{interval}]：").strip() or str(interval))
                    count = int(input("次数 [60]：").strip() or "60")
                    if count < 1:
                        raise ValueError("次数必须为正整数")
                args = argparse.Namespace(command="capture" if choice == "2" else "monitor", host=host,
                    serial=None, timeout=120, profile=path, out="data", allow_rf=True,
                    interval=interval, count=count, temperature_k=None, notes=input("实验备注：").strip())
            else:
                print("请选择 0–7。")
                continue
            directory = vna.run(args)
            if directory:
                from report import create_report
                print("报告：", create_report(directory))
        except KeyboardInterrupt:
            print("\n已停止。保留已完成数据；若未确认 RF OFF，请检查前面板。")
        except Exception as error:
            print("停止：", error)
            print("没有自动重试。")
