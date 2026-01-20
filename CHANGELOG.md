## [0.1.1](https://github.com/bauer-group/CS-GitHubBackup/compare/v0.1.0...v0.1.1) (2026-01-20)

### 🐛 Bug Fixes

* **docker)(deps:** bump python from 3.13-alpine to 3.14-alpine ([b47306a](https://github.com/bauer-group/CS-GitHubBackup/commit/b47306a0c8c5ec360118c5a0d2a6cc3b87c371e7))

## [0.1.0](https://github.com/bauer-group/CS-GitHubBackup/compare/v0.0.0...v0.1.0) (2026-01-20)

### 🚀 Features

* Add backup verification section to README with integrity checks and scripts ([52f5db6](https://github.com/bauer-group/CS-GitHubBackup/commit/52f5db632fb87ae1ba82dad420acabaa88fae48b))
* Add Dependabot configuration and Docker maintenance workflow for automated updates ([10b7874](https://github.com/bauer-group/CS-GitHubBackup/commit/10b7874b0049f0db965df5627eb2e4467e649311))
* Add error logging for backup failures in run_backup function ([003fdc1](https://github.com/bauer-group/CS-GitHubBackup/commit/003fdc1063b6431a92cbc1e8f1fa2759744d6cd7))
* Add Git LFS support for backup and alerting, including size reporting ([c442e0c](https://github.com/bauer-group/CS-GitHubBackup/commit/c442e0c71d15462f962119f81569f7fc478db590))
* Add GITHUB_BACKUP_ALL_ACCESSIBLE setting to backup all accessible repositories ([063175b](https://github.com/bauer-group/CS-GitHubBackup/commit/063175b99a1a73e4a0ab8563c9ae9ab644c7d818))
* Add LFS status reporting to backup results and console output ([01e2c84](https://github.com/bauer-group/CS-GitHubBackup/commit/01e2c84e9cd88f48138371deb2d21c0b423f5474))
* Add MinIO bucket setup script and dependencies ([3d68596](https://github.com/bauer-group/CS-GitHubBackup/commit/3d6859662e6b022725f989456b491c3b93552163))
* Add roadmap and planned features for GitHub Discussions backup ([82eaba1](https://github.com/bauer-group/CS-GitHubBackup/commit/82eaba1d982a614c548d712804c79e6fa05b3d6c))
* Add S3 prefix configuration and update backup key structure ([6380b90](https://github.com/bauer-group/CS-GitHubBackup/commit/6380b905dcda3cfaff9e4123733ddb5875f288ad))
* Add state persistence and S3 prefix configuration to backup process ([2809e56](https://github.com/bauer-group/CS-GitHubBackup/commit/2809e56845e288a9e0bfaa793237277e5214a140))
* Adjust repository fetching to include private repos for organizations ([5a33f7b](https://github.com/bauer-group/CS-GitHubBackup/commit/5a33f7b8927191dfa119c769122b30c029829b7c))
* Always display LFS status in email and Teams alerts for consistency ([dda778a](https://github.com/bauer-group/CS-GitHubBackup/commit/dda778af6375164ab95d54ff384586306dc40d64))
* Enhance alerting system with detailed results and next run time display ([da927a3](https://github.com/bauer-group/CS-GitHubBackup/commit/da927a33ba7feb1960648804825f9a06f0c4ae33))
* Enhance authentication modes documentation and configuration for GitHub Backup ([116a27e](https://github.com/bauer-group/CS-GitHubBackup/commit/116a27e39ef699f8cb0eb68387d62c5350c45441))
* Enhance backup and restore functionality with LFS support and state management improvements ([962b046](https://github.com/bauer-group/CS-GitHubBackup/commit/962b04611da480645358dee33a80e76b0d8a1a7a))
* Enhance logging during repository fetching and scanning process ([ca5ad94](https://github.com/bauer-group/CS-GitHubBackup/commit/ca5ad9446a74cf6b008b6e9fe88557f103a9e7ad))
* Enhance owner resolution for private repositories in GitHubBackupClient ([634a3c3](https://github.com/bauer-group/CS-GitHubBackup/commit/634a3c317abba8b7d8dd7502012972e6239390a3))
* Enhance repository scanning and logging for backup process ([d8660e3](https://github.com/bauer-group/CS-GitHubBackup/commit/d8660e3fed514d6883103a669b67b5fd14b1d397))
* Implement graceful shutdown handling for backup processes ([50c0785](https://github.com/bauer-group/CS-GitHubBackup/commit/50c078590cea809968b6e4b59544f29d5e0637ed))
* Implement repository emptiness check before bundle creation and adjust backup logic ([41e26c9](https://github.com/bauer-group/CS-GitHubBackup/commit/41e26c97dfe38974fe9ba4e9546d465464e54bb2))
* Implement S3 state synchronization in SyncStateManager and update backup process ([1c3b90b](https://github.com/bauer-group/CS-GitHubBackup/commit/1c3b90b65778f578f2d5616a9b25830abe36e5cc))
* Improve wiki URL generation to handle clone URLs without trailing .git correctly ([63beafa](https://github.com/bauer-group/CS-GitHubBackup/commit/63beafa7e56762adacc5c5415debbb40fdb250b9))
* Internal Version 1 (Initial Checkin) ([d48965c](https://github.com/bauer-group/CS-GitHubBackup/commit/d48965c533c51f589eed526d051c79d554e564b5))
* Optimize GitHub client initialization for improved performance with large organizations ([8c4e150](https://github.com/bauer-group/CS-GitHubBackup/commit/8c4e150795ecb0117d09208de7f16bd583dcd297))
* Refactor MinIO bucket setup script to use MinIO SDK instead of boto3 ([07c5d58](https://github.com/bauer-group/CS-GitHubBackup/commit/07c5d5837f46054e8e1dbb742ba30d3bca2d4063))
* Test Cases and Refactor ([4e3eeef](https://github.com/bauer-group/CS-GitHubBackup/commit/4e3eeef5bc21da97da1b633deedcff8d506cc8d2))
* Update alerting system to use 'backup_status' event and enhance documentation ([9ede1a7](https://github.com/bauer-group/CS-GitHubBackup/commit/9ede1a7135cf45a679d0639211d87648c77ea1b6))
* Update backup scheduling to use cron format in configuration and documentation ([eb59bfe](https://github.com/bauer-group/CS-GitHubBackup/commit/eb59bfe210832714e930920ccf123275d7c5b18e))
* Update Git operations to allow caller to handle logging for clone failures and adjust scheduler documentation for cron mode ([325a91c](https://github.com/bauer-group/CS-GitHubBackup/commit/325a91cf36e0b051707fa6fe50416cfc98c1d440))
* Update metadata export limitations and notes for unauthenticated mode in README ([011e19f](https://github.com/bauer-group/CS-GitHubBackup/commit/011e19f34d315970dca8c30ae66465a433d835c2))
* Update release workflow to include additional paths for pull requests and enhance validation jobs ([fbe2de1](https://github.com/bauer-group/CS-GitHubBackup/commit/fbe2de1aa7f086ceeba87f1767dbaa9ee2ebfba2))
* Update S3 key structure for backups to enhance logical browsing and organization ([e501eb1](https://github.com/bauer-group/CS-GitHubBackup/commit/e501eb1e62c292b58811439b48f2448093aec6d3))
* Update S3_PREFIX configuration in README and .env.example for improved path structure ([19710e8](https://github.com/bauer-group/CS-GitHubBackup/commit/19710e8a095e5355cdad21a1bf34840280426115))

### 🐛 Bug Fixes

* Isolate pytest from CI/CD injected environment variables ([8e859b2](https://github.com/bauer-group/CS-GitHubBackup/commit/8e859b2b25f1323ed22edc9d0244424c0813141e))
* Remove success message from pytest output in Dockerfile ([1aea609](https://github.com/bauer-group/CS-GitHubBackup/commit/1aea609bdbbf191e93f68d4e2de5426ccd8beede))
* Test & Runtime ([82416d4](https://github.com/bauer-group/CS-GitHubBackup/commit/82416d4117e7d8056bace9581ae9c1a72512f4b4))
* Update LFS status reporting to use '0' instead of '-' for consistency in email and Teams alerts ([eef9c25](https://github.com/bauer-group/CS-GitHubBackup/commit/eef9c25b1b25f9efb8e81f4c6e2e3dfe602636f2))

### ♻️ Refactoring

* logging to use BackupLogger for consistent console output ([9f41192](https://github.com/bauer-group/CS-GitHubBackup/commit/9f4119201dff00b6b9d0488d7541d8def0a35d01))
