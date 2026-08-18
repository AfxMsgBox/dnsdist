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
| WireGuard 接口 | `wg0` |
| dnsdist 监听地址 | `10.68.0.1:53` |
| WireGuard 网络 | `10.68.0.0/16` |
| Mihomo DNS | `127.0.0.1:253` |
| AliDNS | `223.5.5.5:53`、`223.6.6.6:53` |
| IPv4 / IPv6 ECS | `/24`、`/56` |
| 域名更新时间 | 每 6 小时 |
| Endpoint 检查间隔 | 60 秒 |

修改 [`sys/etc/default/dnsdist-automation`](sys/etc/default/dnsdist-automation) 可以覆盖这些参数。dnsdist 和两个更新服务共用该环境文件。

## 文件布局

```text
sh/
  install.sh
  update-dnsdist-domains.py
  update-dnsdist-ecs.py
  dnsdist_automation/
sys/
  etc/
    default/dnsdist-automation
    dnsdist/
      dnsdist.conf
      generated/
    systemd/system/
tests/
```

`sh/` 存放部署和运行脚本，`sys/` 按目标系统目录组织静态配置。两个 `generated/*.lua` 是安全占位文件，安装后由更新器原子替换，不应手工编辑。

## 关键前提

中央服务器必须把查询源地址识别为对应 Peer 的 WireGuard 地址，例如：

```text
站点 A -> 10.68.0.10
站点 B -> 10.68.0.11
```

如果站点转发后仍显示为 `192.168.x.x` 等局域网客户端地址，Peer 到 ECS 的映射不会命中。各站点需要把发往中央 DNS 的 TCP/UDP 53 流量 SNAT 为自己的 WireGuard 地址。具体 nftables/iptables 规则取决于各站点接口与防火墙结构，本仓库不会自动修改防火墙。

还需要确认：

- Mihomo 已监听 `127.0.0.1:253`。
- `10.68.0.1:53` 未被 AdGuard Home 或其他 DNS 服务占用。
- `/etc/dnsdist/dnsdist.yml` 不存在；dnsdist 2.1+ 可能优先加载它。
- 目标系统使用 systemd，且已安装 Python 3、dnsdist、WireGuard 工具。

## 安装

先检查并修改默认参数，然后执行：

```bash
sudo ./sh/install.sh --install-packages
```

如果依赖已经安装：

```bash
sudo ./sh/install.sh
```

安装脚本会：

1. 复制配置、Python 模块、更新器和 systemd 单元。
2. 保留已存在的 `/etc/default/dnsdist-automation` 和生成规则。
3. 下载域名列表并读取 `wg show wg0 dump`。
4. 运行 `dnsdist --check-config -C /etc/dnsdist/dnsdist.conf`。
5. 启用 dnsdist 与两个 timer。

脚本不会自动停用 AdGuard Home，也不会修改 WireGuard 或防火墙配置。

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
sudo /usr/local/sbin/update-dnsdist-domains.py
sudo /usr/local/sbin/update-dnsdist-domains.py --stats-only
sudo /usr/local/sbin/update-dnsdist-ecs.py
sudo dnsdist --check-config -C /etc/dnsdist/dnsdist.conf
systemctl status dnsdist
systemctl list-timers 'dnsdist-*'
```

`--stats-only` 只下载、解析并输出 JSON 报告，不写文件或重载服务。正常更新器先原子写入新文件，再验证完整主配置；验证或服务重载失败时会恢复旧文件。内容没有变化时不会重载 dnsdist。

测试请求：

```bash
dig @10.68.0.1 00px.net
dig @10.68.0.1 google.com
dig @10.68.0.1 qq.com
```

查看实际 ECS 映射：

```bash
sudo sed -n '1,240p' /etc/dnsdist/generated/ecs-rules.lua
```

## 开发测试

测试完全离线，不会下载真实规则或调用 WireGuard：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q sh tests
```

dnsdist 配置接口参考：[SuffixMatchNode 与服务器配置](https://www.dnsdist.org/reference/config.html)、[规则选择器](https://www.dnsdist.org/reference/selectors.html)、[ECS Action](https://www.dnsdist.org/reference/actions.html)、[配置检查](https://www.dnsdist.org/manpages/dnsdist.1.html)。
