# Douyin Streak

一个面向 Windows 的抖音网页版火花自动续签工具。脚本会复用本机 Edge 或 Chrome 的登录状态，扫描会话列表中带火花标识的好友，并按配置发送表情或文字消息。

> 本项目仅用于学习浏览器自动化和个人效率工具。网页结构变化可能导致脚本失效，请合理控制运行频率，并遵守平台规则。

## 功能

- 自动检查抖音网页版是否已登录。
- 未登录时可打开可见浏览器窗口，引导用户完成扫码或验证。
- 扫描会话列表里的火花好友。
- 通过搜索框切换到指定好友，并在发送前校验聊天标题。
- 支持二选一发送：
  - 表情模式：按关键词在表情栏里查找并点击发送。
  - 文字模式：发送自定义文字。
- 发送后验证聊天记录中出现新的我方消息。
- 最多同时使用 3 个浏览器页面并发处理，提高续火效率。
- 支持 Windows 静默定时运行、错过后补运行、睡眠/休眠唤醒。
- 支持排除指定会话。

## 环境要求

- Windows 10 或 Windows 11
- Python 3.10+
- Microsoft Edge 或 Google Chrome
- Playwright：

```powershell
pip install playwright
```

## 安装

```powershell
git clone https://github.com/brisa-luna/douyin_streak.git
cd douyin_streak
Copy-Item config.example.json config.json
```

## 首次登录

首次使用建议先用可见窗口运行一次：

```powershell
python douyin_streak.py
```

脚本会打开抖音页面并检查登录状态：

- 如果已经登录，会直接开始扫描火花好友。
- 如果没有登录，会等待你扫码或完成验证。
- 如果定时任务在静默模式下发现未登录，会直接退出并写入日志，不会一直卡在后台。

登录状态保存在你本机浏览器用户数据里，仓库不会保存 Cookie、账号或密码。

## 配置发送内容

配置文件是 `config.json`。核心字段如下：

```json
{
  "message": {
    "type": "emoji",
    "emoji_keywords": ["早上好", "续火"],
    "text": "早上好"
  }
}
```

### 发送表情

```json
{
  "message": {
    "type": "emoji",
    "emoji_keywords": ["早上好", "续火"]
  }
}
```

脚本会按顺序查找关键词。比如先找名称包含“早上好”的表情，找不到再找名称包含“续火”的表情。

### 发送文字

```json
{
  "message": {
    "type": "text",
    "text": "早上好"
  }
}
```

文字模式会把 `text` 写入聊天输入框并按 Enter 发送。

## 配置发送时间

```json
{
  "schedule": {
    "hour": 9,
    "minute": 0
  }
}
```

这里使用 24 小时制。比如晚上 22:30：

```json
{
  "schedule": {
    "hour": 22,
    "minute": 30
  }
}
```

修改时间后，重新创建一次 Windows 定时任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\schedule_task.ps1
```

## 其他常用配置

```json
{
  "parallel": {
    "enabled": true,
    "max_concurrent": 3
  },
  "targets": {
    "excluded_names": []
  },
  "behavior": {
    "headless": true,
    "close_browser_after": true,
    "max_retries": 3
  },
  "login": {
    "login_wait_seconds": 180
  },
  "run_state": {
    "skip_if_success_today": true
  }
}
```

- `max_concurrent`：并发工作页面数量，脚本最高限制为 3。
- `excluded_names`：不发送消息的会话名称。
- `headless`：`true` 表示不显示浏览器窗口，适合定时任务；首次登录建议设为 `false`。
- `close_browser_after`：运行结束后是否关闭脚本启动的浏览器。
- `max_retries`：单个好友发送失败后的重试次数。
- `login_wait_seconds`：未登录时等待扫码/验证的秒数。
- `skip_if_success_today`：当天已经成功运行过时，后续触发会直接跳过，避免重复发送。

## 创建每日定时任务

在 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\schedule_task.ps1
```

任务行为：

- 按 `config.json` 中的 `schedule.hour` 和 `schedule.minute` 每天运行。
- 使用 `run_silent.vbs` 静默启动。
- 允许睡眠或休眠时唤醒电脑。
- 错过运行时间后，会在电脑恢复可用时补运行。
- 同一时间只允许一个实例运行。
- 如果当天已经成功运行过，再次触发会跳过，避免重复续火花。

完全关机时，普通 Windows 计划任务无法唤醒电脑。

## 管理定时任务

暂停：

```powershell
Disable-ScheduledTask -TaskName "DouyinStreakRenewal"
```

恢复：

```powershell
Enable-ScheduledTask -TaskName "DouyinStreakRenewal"
```

删除：

```powershell
Unregister-ScheduledTask -TaskName "DouyinStreakRenewal" -Confirm:$false
```

也可以按 `Win + R`，输入 `taskschd.msc`，在“任务计划程序库”中管理 `DouyinStreakRenewal`。

## 许可证

[MIT License](LICENSE)
