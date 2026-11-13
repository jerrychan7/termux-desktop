# XFCE 显示设置的 Scale 在 termux-x11 上无效

状态：已确认（2026-11-13）。这是显示服务端的 RandR 能力缺口，desk 无法在这一层修复。

## 现象

在 XFCE 的「设置 → 显示」里，内置输出 `builtin` 的 Scale 可以调整，点击应用后画面没有任何变化；分辨率、刷新率等其它选项正常。

本机环境：XFCE 4.20（xfce4-settings 4.20.5），显示服务是 `termux-x11-nightly`（Termux:X11 自带的 X server），所有验证都在 Termux 本体完成。

## 结论

XFCE 4.20 的 Scale 不在应用层做，而是交给 X server 的 RandR CRTC transform。`xfsettingsd` 应用配置时调用 `XRRSetCrtcTransform`；termux-x11 的 CRTC 拒绝该请求，缩放被丢弃，所以界面不变，也没有错误弹窗。

## 证据

调整 Scale 的时间点，会话日志（`desk logs`）里出现：

```text
xfsettingsd-WARNING **: 00:41:32.997: Failed to temporarily disable CRTC 63.
xfsettingsd-WARNING **: 00:41:32.997: Failed to apply the scale, maybe the CRTC does not support transforms
xfsettingsd-WARNING **: 00:41:36.908: Failed to apply the scale, maybe the CRTC does not support transforms
```

对应配置：`xfconf-query -c displays -l` 有 `/Default/builtin/Scale`；`displays.xml` 里保存的值是 1.0。

## 机制（xfce4-settings 源码路径）

- 显示对话框：`dialogs/display-settings/display-settings-x11.c` 的 `xfce_display_settings_x11_get_scale` 返回 `1.0 / randr->scalex[output_id]`；`set_scale` 把 `scalex`、`scaley` 都设成 `1.0 / scale`，保存进 xfconf 的 `/%s/%s/Scale`。
- 应用配置：`xfsettingsd/displays-x11.c` 的 `xfce_displays_helper_x11_apply_crtc_transform` 构造 `XTransform`（`matrix[0][0]=scalex`、`matrix[1][1]=scaley`、`matrix[2][2]=1`），缩放不为 1 时用 bilinear 过滤，否则用 nearest，然后调用 `XRRSetCrtcTransform`；失败时打印上面的警告并继续。
- 读取当前状态：`common/xfce-randr.c` 用 `XRRGetCrtcTransform` 读回 `scalex`/`scaley`。

## 影响

- 「显示」里的 Scale 在本机永远无效，与用户是否点击应用无关。
- 需要放大界面时，用 Termux:X11 应用自身的 Display → Scale（客户端侧放大，整个输出一起放大，应用不感知 HiDPI），或调整分辨率。
- desk 不在这层做封装：XFCE 已经正确把请求交给了 X server，问题在服务端能力。

## 将来

如果 termux-x11 实现了 RandR CRTC transform，XFCE 的 Scale 会直接恢复，不需要改 desk。
