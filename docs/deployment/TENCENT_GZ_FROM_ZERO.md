# Tencent Guangzhou Deployment From Zero

Current operator state:

- Tencent Lighthouse already purchased.
- Server IP: `<SERVER_IP>` (keep the real value only in the ignored local file)
- SSH user: `ubuntu`
- Ubuntu 24.04 and Docker are already installed.
- ICP filing is under review.
- No public domain may be activated yet.

## Local Package Preparation

After the two deployment commits and a clean workspace:

```powershell
.\scripts\deployment\tencent-gz\00-check-local.ps1
.\scripts\deployment\tencent-gz\01-build-package.ps1
.\scripts\deployment\tencent-gz\02-verify-package.ps1 -PackagePath "<PACKAGE_PATH>"
```

The package is produced from `git archive HEAD`, not by recursively copying
the working directory.

## Later Manual SSH Stage

This repository task does not execute these commands. After local acceptance:

```powershell
Copy-Item .deploy\tencent-gz.local.example.ps1 .deploy\tencent-gz.local.ps1
# Edit only the ignored local file.
.\scripts\deployment\tencent-gz\03-upload-package.ps1
.\scripts\deployment\tencent-gz\04-open-ssh.ps1
```

On the server, extract the verified package under `/opt/medtrust`, then:

```bash
sudo deploy/tencent-gz-public-alpha/bootstrap-server.sh
sudo deploy/tencent-gz-public-alpha/init-secrets.sh
sudo deploy/tencent-gz-public-alpha/deploy-pre-icp.sh
sudo deploy/tencent-gz-public-alpha/create-admin.sh
sudo deploy/tencent-gz-public-alpha/security-check.sh
```

Do not upload local databases, `.env`, Docker volumes, Phase 5.13 evidence,
patient data, browser profiles, or local keys.
