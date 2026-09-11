# Linux x86_64 FE/BE 组包

`package_release.sh` 只将**已经成功完成、之后未启动或修改**的 `build.sh` 输出组包；不编译、不启动服务、不生成测试预期，不操作生产安装目录。输入必须明确指定源码 checkout、FE/BE output、一个尚不存在的交付目录和真实构建记录。依赖 Bash、Python 3.8+ 和 Git。

先保存构建的实际命令、退出码、提交和选项。构建失败时不要填写成功记录；`build_exit_code` 非 0 会被拒绝。构建记录是调用者提供的证据，脚本不能从已有二进制独立证明编译过程成功或确认二进制一定来自该提交。

```json
{
  "source_commit": "填写实际构建的完整40位提交SHA",
  "build_exit_code": 0,
  "build_type": "Release",
  "build_command": "填写实际成功的命令，包含所有构建步骤",
  "completed_at_utc": "填写实际结束时间",
  "options": {
    "USE_AVX2": "填写实际值",
    "BUILD_TYPE": "Release",
    "compiler_and_image": "填写编译器和构建镜像/系统版本"
  }
}
```

源码 checkout 必须与记录中的 SHA 一致且没有 tracked 或 non-ignored untracked 改动；构建中仍修改源码或发布文件时不要运行。将 JSON 放在仓库外，再替换下列示例路径：

```bash
bash tools/null-safe-join/package_release.sh \
  --source /work/doris-4.1.4-nullsafe \
  --output /work/doris-4.1.4-nullsafe/output \
  --destination /artifacts/doris-4.1.4-nullsafe-delivery \
  --build-record /artifacts/build-record.json
```

destination 的父目录应已存在，且目标目录必须位于源码和 output 之外。已有目标一律拒绝，不覆盖旧包。可用 `--name doris-fork-4.1.4-nullsafe-linux-x86_64` 自定归档根目录名，默认包含源码短 SHA、架构及构建模式。

脚本参照仓库 `build.sh` 的 `copy_common_files` 和 FE/BE 输出逻辑，复制发行目录，包括每个组件的 `LICENSE-dist.txt`、`NOTICE.txt`、`licenses/`、JAR/库、启动脚本和配置。官方包还可包含 MS、Broker 等组件，本脚本**只交付 FE/BE**，不能用于声称已组装完整 Cloud 发行包。

空的 log、doris-meta、storage、temp_dir 等运行目录不打包；这些目录有文件、出现未知组件入口或发行目录内混入日志/PID 时拒绝。相对软链保留，但必须在组件内部可解析；拒绝外部/绝对软链和特殊文件。配置按输入 output 原样保留，因此必须使用未运行过的构建输出，不能把已部署集群目录当输入。

必检项包括非空 FE/BE 启停脚本、配置、许可文件，实际 `be/lib/doris_be` 的可执行权限及 ELF64 little-endian `EM_X86_64=62`，以及 `fe/lib/doris-fe.jar` 的 ZIP CRC 和 `DorisFE.class`。这只是组包前置检查，不检测所有依赖是否完整，不证明 CPU 支持 AVX2、目标 glibc/动态库兼容或服务能启动。

成功后交付目录包含：

- `*.tar.gz`：根目录内为 FE/BE 和 `package-manifest/`。
- `*.tar.gz.sha256`：完整压缩包摘要。
- 外置 `package-manifest/BUILD-INFO.json`：源码 SHA、ELF 架构、调用者构建模式/命令/选项、检查范围。
- `package-manifest/FILES.json`：文件及目录清单、权限、软链目标、普通文件 SHA-256；覆盖 payload 和 BUILD-INFO，自身及 SHA256SUMS 不递归自校验。
- `package-manifest/SHA256SUMS`：普通文件摘要（含 FILES.json），路径相对于解压后的归档根目录。软链目标和权限通过 FILES.json 留档，完整归档另有摘要。

```bash
# 在交付目录中验证完整归档；文件名替换为实际值。
sha256sum -c doris-fork-....tar.gz.sha256
# 解压后进入归档根目录，再验证普通文件。
sha256sum -c package-manifest/SHA256SUMS
```

本脚本初次交付只做 `bash -n`、嵌入 Python 语法检查和“不完整 output 必须拒绝”检查；真实包正向测试等待完整构建产物。组包成功后仍需记录实际 FE/BE 版本、Linux x86_64 启动、BE 单测、官方 suite、业务探针及升级演练结果，参见 [升级评估](../../docs/development/4.1.4-upgrade-notes.md)。
