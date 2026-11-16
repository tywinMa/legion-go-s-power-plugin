import asyncio
import json
import os
import pwd
import stat
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

DMI = Path("/sys/class/dmi/id")
FIRMWARE = Path("/sys/class/firmware-attributes")
PLATFORM = Path("/sys/class/platform-profile")
AC = Path("/sys/class/power_supply/ACAD/online")
HWMON = Path("/sys/class/hwmon")
ATTRIBUTES = ("ppt_pl1_spl", "ppt_pl2_sppt", "ppt_pl3_fppt")


def read(path):
    return path.read_text().strip()


def suspend_offset():
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()


class Hardware:
    def __init__(self):
        if read(DMI / "sys_vendor") != "LENOVO" or read(DMI / "product_name") != "83Q2":
            raise OSError("此版本适用于 Legion Go S 83Q2（Ryzen Z2 Go）。")
        profiles = [p for p in PLATFORM.iterdir() if read(p / "name") == "lenovo-wmi-gamezone"]
        attributes = [p / "attributes" for p in FIRMWARE.glob("lenovo-wmi-other*")
                      if all((p / "attributes" / name / "current_value").is_file() for name in ATTRIBUTES)]
        if len(profiles) != 1 or len(attributes) != 1:
            raise OSError("未找到可用的联想功率控制接口。")
        self.profile = profiles[0] / "profile"
        self.choices = read(profiles[0] / "choices").split()
        self.paths = [attributes[0] / name for name in ATTRIBUTES]
        if os.geteuid() != 0 or not all(os.access(path, os.W_OK) for path in
                                            [self.profile, *(p / "current_value" for p in self.paths)]):
            raise PermissionError("Decky 后端没有写入联想功率接口的权限。请检查 Decky Loader 服务是否以 root 运行。")
        self.bounds = [(int(read(p / "min_value")), int(read(p / "max_value"))) for p in self.paths]
        if "custom" not in self.choices or not all(int(read(p / "scalar_increment")) == 1 for p in self.paths):
            raise OSError("当前固件没有兼容的自定义功率档。")
        self.minimum = max(lo for lo, _ in self.bounds)
        self.maximum = min(hi for _, hi in self.bounds)
        if self.minimum > self.maximum:
            raise OSError("固件没有可同时设置三项功率上限的瓦数。")
        self.sensor = next((p / "power1_input" for p in HWMON.glob("hwmon*")
                            if (p / "power1_label").is_file() and read(p / "power1_label") == "PPT"), None)

    def limits(self):
        return tuple(int(read(p / "current_value")) for p in self.paths)

    def set_profile(self, profile, settle=True):
        if profile not in self.choices:
            raise ValueError("无效的联想性能档。")
        if read(self.profile) != profile:
            self.profile.write_text(f"{profile}\n")
            # The EC handles platform mode changes asynchronously.
            if settle:
                time.sleep(1.1)
            if read(self.profile) != profile:
                raise OSError("联想性能档未确认切换。")

    def set_limits(self, values, timeout=4):
        if read(self.profile) != "custom":
            raise OSError("功率上限需要联想自定义性能档。")
        before = self.limits()
        # Raise peak limits first and lower sustained limits first.
        order = sorted(range(len(values)), key=lambda i: -i if values[i] > before[i] else i)
        for i in order:
            lo, hi = self.bounds[i]
            if type(values[i]) is not int or not lo <= values[i] <= hi:
                raise ValueError("功率上限超出固件范围。")
        for i in order:
            if values[i] != before[i]:
                (self.paths[i] / "current_value").write_text(f"{values[i]}\n")
        # This BIOS reports the previous values for a short time after a write.
        deadline = time.monotonic() + timeout
        while True:
            if read(self.profile) != "custom":
                raise OSError("联想性能档在写入期间发生变化。")
            actual = self.limits()
            if actual == values:
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.125)
        raise OSError(f"固件未确认功率上限，读回 {' / '.join(map(str, actual))} W。")

    def power(self):
        try:
            return round(int(read(self.sensor)) / 1_000_000, 1) if self.sensor else None
        except (OSError, ValueError):
            return None


class Plugin:
    async def _main(self):
        self.lock = asyncio.Lock()
        self.file = Path(os.environ["DECKY_PLUGIN_SETTINGS_DIR"]) / "power.json"
        self.hardware = None
        self.error = None
        self.settings_error = None
        self.context = None
        self.offset = suspend_offset()
        self.next_retry = 0
        self.retry_delay = 2
        self.applied = False
        async with self.lock:
            try:
                self.hardware = await asyncio.to_thread(Hardware)
            except (OSError, ValueError) as exc:
                self.error = str(exc)
            self.config = self._load()
            if self.hardware and not self.config["enabled"] and self.config.get("baseline"):
                await asyncio.to_thread(self._finish_restore)
            self.task = asyncio.create_task(self._watch())

    def _load(self):
        defaults = {"enabled": False, "global": {}, "baseline": None}
        try:
            fd = os.open(self.file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, encoding="utf-8") as stream:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("设置文件须为普通文件。")
                data = json.load(stream)
                if (not isinstance(data, dict) or type(data.get("enabled")) is not bool
                        or not isinstance(data.get("global"), dict)):
                    raise ValueError("设置文件格式无效。")
                try:
                    self._set_file_owner(fd, info)
                except (OSError, KeyError) as exc:
                    self.settings_error = f"已读取设置，但未能修复设置文件权限：{exc}"
        except FileNotFoundError:
            return defaults
        except (OSError, ValueError) as exc:
            self.settings_error = f"无法读取已保存的设置：{exc}"
            return defaults
        baseline = data.get("baseline")
        if baseline is not None and (
            not isinstance(baseline, dict) or not isinstance(baseline.get("profile"), str)
            or not isinstance(baseline.get("limits"), list) or len(baseline["limits"]) != len(ATTRIBUTES)
            or any(type(v) is not int for v in baseline["limits"])
        ):
            data["baseline"] = None
        if "games" in data:
            del data["games"]
            try:
                self._save(data)
            except OSError as exc:
                self.settings_error = f"无法更新配置文件：{exc}"
        return data

    def _set_file_owner(self, fd, info):
        if os.geteuid() != 0:
            return
        owner = pwd.getpwnam(os.environ["DECKY_USER"])
        if (info.st_uid, info.st_gid) != (owner.pw_uid, owner.pw_gid):
            os.fchown(fd, owner.pw_uid, owner.pw_gid)
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(fd, 0o600)

    def _save(self, data):
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.file.parent, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    json.dump(data, stream, ensure_ascii=False)
                    stream.flush()
                    self._set_file_owner(stream.fileno(), os.fstat(stream.fileno()))
                    os.fsync(stream.fileno())
                    temporary.replace(self.file)
                finally:
                    temporary.unlink(missing_ok=True)
        except (OSError, KeyError) as exc:
            raise OSError(f"无法保存功率设置：{exc}") from exc
        self.config = data
        self.settings_error = None

    def _saved_limits(self):
        settings = self.config["global"]
        lo, hi = self.hardware.minimum, self.hardware.maximum
        return {
            source: max(lo, min(hi, settings.get(source, default)
                               if type(settings.get(source)) is int else default))
            for source, default in (("battery", 15), ("ac", 20))
        }

    def _select(self):
        source = "ac" if read(AC) == "1" else "battery"
        return source, self._saved_limits()[source]

    def _acquire(self):
        device = self.hardware
        baseline = self.config.get("baseline")
        if baseline is not None:
            device.set_profile("custom")
            return
        profile = read(device.profile)
        try:
            device.set_profile("custom")
            baseline = {"profile": profile, "limits": device.limits()}
            self._save({**self.config, "baseline": baseline})
        except (OSError, ValueError):
            device.set_profile(profile)
            raise

    def _restore(self):
        baseline = self.config.get("baseline")
        if not self.hardware or not baseline:
            return
        if read(self.hardware.profile) == "custom":
            try:
                self.hardware.set_limits(tuple(baseline["limits"]))
            finally:
                self.hardware.set_profile(baseline["profile"], settle=False)
        self._save({**self.config, "baseline": None})
        self.applied = False

    def _tick(self):
        if not self.hardware or not self.config.get("enabled"):
            return
        before = None
        try:
            source, target = self._select()
            offset = suspend_offset()
            context = (source, target)
            changed = context != self.context or abs(offset - self.offset) > 1
            self.context = context
            self.offset = offset
            now = time.monotonic()
            if changed:
                self.next_retry = 0
                self.retry_delay = 2
            profile = read(self.hardware.profile)
            values = (target,) * len(ATTRIBUTES)
            if profile == "custom" and self.hardware.limits() == values and self.config.get("baseline"):
                if now >= self.next_retry + 30:
                    self.retry_delay = 2
                self.applied = True
                self.error = None
                return
            self.applied = False
            if now < self.next_retry:
                if not self.error:
                    self.error = "检测到功率上限被覆盖，正在重新应用已保存的设置。"
                return
            self.next_retry = now + self.retry_delay
            self.retry_delay = min(self.retry_delay * 2, 30)
            self._acquire()
            before = self.hardware.limits()
            self.hardware.set_limits(values)
            self.applied = True
            self.error = None
        except (OSError, ValueError) as exc:
            self.applied = False
            self.error = str(exc)
            if before is not None:
                try:
                    if read(self.hardware.profile) == "custom":
                        self.hardware.set_limits(before)
                except (OSError, ValueError) as rollback:
                    self.error += f" 回退失败：{rollback}"

    async def _watch(self):
        while True:
            async with self.lock:
                await asyncio.to_thread(self._tick)
            await asyncio.sleep(2)

    def _state(self):
        state = {"available": bool(self.hardware), "enabled": self.config.get("enabled", False),
                 "active": False, "error": self.settings_error or self.error, "canRestore": bool(self.config.get("baseline")),
                 "source": None, "limits": None, "power": None, "target": None}
        if self.hardware:
            device = self.hardware
            state.update(minimum=device.minimum, maximum=device.maximum, globalLimits=self._saved_limits())
            try:
                source, target = self._select()
                profile = read(device.profile)
                limits = device.limits() if profile == "custom" else None
                state.update(source=source, target=target if state["enabled"] else None, limits=limits,
                             profile=profile, power=device.power(),
                             active=self.applied and profile == "custom" and limits == (target,) * len(ATTRIBUTES))
            except (OSError, ValueError) as exc:
                state["error"] = str(exc)
        return state

    async def get_state(self):
        async with self.lock:
            return await asyncio.to_thread(self._state)

    def _save_limits(self, battery, ac):
        if not self.hardware:
            raise OSError(self.error or "功率控制接口不可用。")
        settings = {"battery": battery, "ac": ac}
        if any(type(v) is not int or not self.hardware.minimum <= v <= self.hardware.maximum
               for v in settings.values()):
            raise ValueError("瓦数超出固件范围。")
        self._save({**self.config, "global": settings})
        self.context = None
        self._tick()
        return self._state()

    async def save_limits(self, battery, ac):
        async with self.lock:
            return await asyncio.to_thread(self._save_limits, battery, ac)

    def _finish_restore(self):
        try:
            self._restore()
            self.error = None
        except (OSError, ValueError) as exc:
            self.error = f"恢复原设置失败：{exc}"

    def _set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("无效的功率限制开关状态。")
        if not self.hardware:
            raise OSError(self.error or "功率控制接口不可用。")
        self._save({**self.config, "enabled": enabled})
        if enabled:
            self.context = None
            self._tick()
        else:
            self._finish_restore()
        return self._state()

    async def set_enabled(self, enabled):
        async with self.lock:
            return await asyncio.to_thread(self._set_enabled, enabled)

    async def _unload(self):
        async with self.lock:
            self.task.cancel()
