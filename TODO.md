# TODO

## 发布前验证

- [ ] 在全新 Debian/Ubuntu 主机上执行单文件安装，确认所有 WireGuard 接口状态均会列出、Mihomo DNS 与 HTTP/混合代理自动检测、代理选择可保存或拒绝、完整 IP ECS、查询日志开关及 journal 输出、环境检查、无开发测试部署、安装中断后可重试、默认与非默认监听端口正常、dnsdist 1.7.3 后缀选择器、ECS `NetmaskGroupRule` 及 `LogAction` 兼容、旧版单元可安全迁移，且不会覆盖无关同名文件，并确认安装后 dnsdist 已运行且开机自启。
- [ ] 使用实际 GitHub 压缩包分别通过直连和 Mihomo 代理执行更新，再人为制造一次失败并验证回滚；确认程序包与全部域名来源使用同一代理、删除 `DNSDIST_DOWNLOAD_PROXY` 后恢复直连、临时构建树中的 systemd 单元只引用最终安装目录，且失败日志在回滚前完整输出。
- [ ] 分别验证默认卸载和 `--purge` 彻底卸载，确认 timer、运行中的更新服务和 dnsdist 均先停止，且 dnsdist 开机自启已关闭。

后续可选改进项参见 `ARCH.md` 的“可演进方向”。新增未完成任务时，应在此记录范围、原因和验收条件。
