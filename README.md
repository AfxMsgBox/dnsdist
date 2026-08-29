# dnsdist WireGuard 策略 DNS

这套配置把 dnsdist 用作中央 DNS 策略路由器：

```text
WireGuard 客户端 -> dnsdist :53
                       |
                       +-- 广告域名 -> NXDOMAIN
                       +-- 代理域名 -> Mihomo 127.0.0.1:253
                       `-- 其他域名 -> 根据 Peer Endpoint 设置 ECS -> AliDNS
```

规则优先级固定为：**广告规则 > 代理规则 > AliDNS**。配置未启用 dnsdist packet cache，以避免不同 Peer 的 ECS 响应互相污染。

## 默认参数

| 参数 | 默认值 |
| --- | --- |
| WireGuard 接口 | `wg-pub` |
| dnsdist 监听地址 | `10.133.0.1:53` |
| WireGuard 网络 | `10.133.0.0/24` |
| Mihomo DNS | `127.0.0.1:253` |
| AliDNS | `223.5.5.5:53`、`223.6.6.6:53` |
| IPv4 / IPv6 ECS | `/24`、`/56` |
| 域名更新时间 | 每 6 小时 |
| Endpoint 检查间隔 | 60 秒 |

安装时会逐项提示全部参数，直接回车即可使用默认值。安装后的本机参数位于 `/opt/mydnsdist/config/dnsdist-automation`；dnsdist 和两个更新服务共用该文件。

## 文件布局

```text
ARCH.md
TODO.md
config/
  dnsdist-automation
  dnsdist.conf
generated/
  domain-rules.lua
  ecs-rules.lua
sh/
  install.sh
  install-common.sh
  manage-config.py
  uninstall.sh
  update.sh
  update-dnsdist-domains.py
  update-dnsdist-ecs.py
  dnsdist_automation/
systemd/
  10-dnsdist-automation.conf
  dnsdist-domain-update.service
  dnsdist-domain-update.timer
  dnsdist-ecs-update.service
  dnsdist-ecs-update.timer
tests/
  test_install.py
  test_manage_config.py
  test_uninstall.py
  test_update.py
```

仓库根目录与安装目录一一对应，默认完整安装到 `/opt/mydnsdist`。`sh/` 存放部署和运行脚本，`config/` 保存本机配置，`generated/` 保存运行时规则，`systemd/` 保存服务单元。两个 `generated/*.lua` 初始为安全占位文件，安装后由更新器原子替换，不应手工编辑。

系统目录仅保留必要的软链接：dnsdist 主配置链接到安装目录，systemd 单元链接到 `systemd/`。项目文件不会复制到 `/usr/local`、`/etc/default` 或其他分散目录。

## 关键前提

中央服务器必须把查询源地址识别为对应 Peer 的 WireGuard 地址，例如：

```text
站点 A -> 10.133.0.10
站点 B -> 10.133.0.2
```

如果站点转发后仍显示为 `192.168.x.x` 等局域网客户端地址，Peer 到 ECS 的映射不会命中。各站点需要把发往中央 DNS 的 TCP/UDP 53 流量 SNAT 为自己的 WireGuard 地址。具体 nftables/iptables 规则取决于各站点接口与防火墙结构，本仓库不会自动修改防火墙。

还需要确认：

- Mihomo 已监听 `127.0.0.1:253`。
- `10.133.0.1:53` 未被 AdGuard Home 或其他 DNS 服务占用。
- `/etc/dnsdist/dnsdist.yml` 不存在；dnsdist 2.1+ 可能优先加载它。
- 目标系统使用 systemd，且已安装 Python 3、dnsdist、WireGuard 工具。

## 安装

只需下载一个安装脚本，不需要安装 Git：

```bash
wget -O install.sh https://raw.githubusercontent.com/AfxMsgBox/dnsdist/main/sh/install.sh
sudo bash install.sh
```

安装脚本默认建议使用 `/opt/mydnsdist`，并依次提示本机参数。直接回车使用默认值，也可以输入其他不含空白字符的绝对安装路径。非交互安装使用：

```bash
sudo bash install.sh --non-interactive
```

安装脚本会优先使用 `wget` 下载 GitHub `main` 分支压缩包，没有 `wget` 时回退到 `curl`。脚本不会自动安装任何软件；缺少 dnsdist、WireGuard 工具、Python 3、下载工具或其他基础依赖时，会列出缺失命令和建议的手动安装命令，然后退出。

如果安装目录中已有可识别的完整项目结构，单文件安装器会保留本机参数、当前生成规则和供应商配置备份，原子刷新程序文件后继续安装。因此前一次安装在系统集成或服务启动阶段失败时，可以直接重新运行刚下载的 `install.sh`。非空目录不符合项目结构时仍会拒绝覆盖。

从早期分散目录版本升级时，安装器只会自动替换内容特征能够确认属于本项目的旧 systemd 单元；无法确认归属的同名文件或软链接不会被覆盖。旧 `/etc/default/dnsdist-automation` 参数会在首次集中安装时迁移。

安装过程会：

1. 提示并建立集中安装目录。
2. 检查必要软件；缺少时仅给出手动安装提示并退出。
3. 下载并检查完整源码压缩包。
4. 提示全部运行参数并验证地址、网络、端口和阈值。
5. 检查 `DNSDIST_WG_DNS_IP:53` 是否被其他程序占用；冲突时显示占用信息并退出。
6. 自动识别 dnsdist 服务运行组并设置权限。
7. 迁移可确认归属的旧版单元，只在系统目录建立必要软链接。
8. 下载域名列表并读取 `wg show wg-pub dump`。
9. 检查完整 dnsdist 配置，启动 dnsdist 和两个 timer。

脚本不会自动停用 AdGuard Home，也不会修改 WireGuard 或防火墙配置。如果 AdGuard Home 等程序监听通配地址的 53 端口，必须先调整其监听地址或停止该服务。

## 更新

更新不依赖 Git，会重新下载 GitHub 压缩包：

```bash
sudo /opt/mydnsdist/sh/update.sh
```

更新器先在同一文件系统的临时目录中验证脚本、Python 代码、离线测试和 dnsdist 配置，再切换安装目录。本机 `config/dnsdist-automation` 中的已有值与当前生成规则会保留，新版本增加的参数会使用新版默认值自动补充。服务启动失败时自动恢复上一版本。

如需更新时重新逐项确认参数：

```bash
sudo /opt/mydnsdist/sh/update.sh --configure
```

## 卸载

默认卸载会停止并禁用 dnsdist 及本项目的更新定时器，移除系统软链接，但保留集中安装目录：

```bash
sudo ./sh/uninstall.sh
```

如需同时删除完整集中安装目录：

```bash
sudo ./sh/uninstall.sh --purge
```

执行前可预览全部操作；预览模式不要求 root 权限：

```bash
./sh/uninstall.sh --dry-run
./sh/uninstall.sh --purge --dry-run
```

卸载脚本不会卸载 dnsdist、WireGuard、Python 或 Mihomo 软件包，也不会修改 WireGuard 和防火墙。`--purge` 会删除运行该脚本所在的完整安装目录，因此不要在安装目录中存放无关文件。

## 规则来源

代理域名默认合并以下列表；任一下载失败或解析结果为空都会中止本次更新，避免静默丢失规则：

- `AfxMsgBox/MyRule` 的 `myproxylist.txt` 和 `gpt.txt`
- `Loyalsoldier/clash-rules` 的 `gfw.txt` 和 `tld-not-cn.txt`

Clash 的 `DOMAIN` 会保留为精确匹配，`DOMAIN-SUFFIX` 和 `+.` 条目转换为后缀树，`DOMAIN-REGEX` 则应用与广告规则相同的安全降级和固定后缀预筛选。

广告规则默认来自 AdGuard DNS Filter。解析器支持：

```text
example.com
||example.com^
||*-ad.example.com^
/^admaster\./
0.0.0.0 example.com
@@||allowed.example^
||important.example^$important
||ipv4-only.example^$dnstype=A
```

转换器按语义分别生成 dnsdist 数据结构：

- 普通域名和 hosts 格式是精确匹配，使用 `DNSNameSet` / `QNameSetRule`。
- `||example.com^` 及代理列表域名是后缀匹配，使用 `SuffixMatchNode` / `QNameSuffixRule`。
- 能证明等价的正则会降级为精确或后缀匹配；其余正则保留为 `RegexRule`。如果能提取必需的固定域名后缀，会先经过后缀树预筛选再运行正则。
- 支持 AdGuard 的例外规则、`important`、`badfilter` 和正向 `dnstype`。带有其他修饰符的规则不会被错误简化，而是跳过并计入报告。

所有集合都会去重；后缀树会删除已被父级覆盖的子域名，精确集合会删除已被同组后缀覆盖的条目。生成文件只返回局部规则表，主配置注册规则后主动回收构建期间的临时 Lua 对象。

每次更新会向标准输出写一行 JSON 统计。若保留的广告正则超过 `DNSDIST_MAX_AD_REGEX_RULES`，或不支持规则占比超过 `DNSDIST_MAX_UNSUPPORTED_AD_RATIO`，更新会失败并保留上一版规则。默认值分别为 `2048` 和 `0.05`。

## ECS 生成逻辑

`update-dnsdist-ecs.py` 解析 `wg show INTERFACE dump`：

1. 只处理位于 `DNSDIST_WG_NETWORK` 内的 AllowedIPs。
2. 默认拒绝私网、回环、保留地址等非公网 Endpoint。
3. 把 IPv4 Endpoint 归一为 `/24`，IPv6 Endpoint 归一为 `/56`。
4. 为每个 Peer 生成 `SetECSOverrideAction`、`SetECSAction` 和 `PoolAction("china-ecs")`。
5. 没有可用 Endpoint 的 Peer 落入 `china-noecs`。

如果确实要把非公网 Endpoint 用作 ECS，可在环境文件中设置：

```bash
DNSDIST_ECS_ALLOW_NON_GLOBAL=1
```

## 手动检查

```bash
sudo /opt/mydnsdist/sh/update-dnsdist-domains.py
sudo /opt/mydnsdist/sh/update-dnsdist-domains.py --stats-only
sudo /opt/mydnsdist/sh/update-dnsdist-ecs.py
sudo dnsdist --check-config -C /opt/mydnsdist/config/dnsdist.conf
systemctl status dnsdist
systemctl list-timers 'dnsdist-*'
```

`--stats-only` 只下载、解析并输出 JSON 报告，不写文件或重载服务。正常更新器先原子写入新文件，再验证完整主配置；验证或服务重载失败时会恢复旧文件。内容没有变化时不会重载 dnsdist。

测试请求：

```bash
dig @10.133.0.1 00px.net
dig @10.133.0.1 google.com
dig @10.133.0.1 qq.com
```

查看实际 ECS 映射：

```bash
sudo sed -n '1,240p' /opt/mydnsdist/generated/ecs-rules.lua
```

## 开发测试

测试完全离线，不会下载真实规则或调用 WireGuard：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q sh tests
bash -n sh/install.sh sh/install-common.sh sh/update.sh sh/uninstall.sh
```

dnsdist 配置接口参考：[SuffixMatchNode 与服务器配置](https://www.dnsdist.org/reference/config.html)、[规则选择器](https://www.dnsdist.org/reference/selectors.html)、[ECS Action](https://www.dnsdist.org/reference/actions.html)、[配置检查](https://www.dnsdist.org/manpages/dnsdist.1.html)。
