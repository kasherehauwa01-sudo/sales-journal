#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
# Default is validation ONLY. Never changes budgets, manifests, DB schema or services.
mode=${1:---check}
[[ "$mode" == --check || "$mode" == --apply ]] || { echo 'Usage: install-calltrack.sh [--check|--apply]'; exit 2; }
root=/var/www/html/vr/calltrack-v3-20261026
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for tool in php patch sha256sum flock; do command -v "$tool" >/dev/null || { echo "Missing tool: $tool"; exit 1; }; done
[[ -d "$root/api" && -f "$root/api/manager_tasks_service.php" ]] || { echo 'Calltrack source not found'; exit 1; }
actual=$(sha256sum "$root/api/manager_tasks_service.php"); actual=${actual%% *}
[[ "$actual" == 8f6382d514f0ad4d7e22bacf9b92ce24b5222c16e003065f4cfa10c567f58f7d ]] || { echo 'Unsupported source: obtain a new reviewed patch; do not refresh hashes'; exit 1; }
actual=$(sha256sum "$root/api/analytics_integration.php"); actual=${actual%% *}
[[ "$actual" == abfbef560abcda54b3c479d1f6cf731b687aa09e685f37c187cecd6e1a01140d ]] || { echo 'Unsupported analytics integration source; review actual file before patching'; exit 1; }
for file in analytics_permanent.php analytics_permanent_cli.php; do
    [[ ! -e "$root/api/$file" ]] || { echo 'Target exists; review previous installation'; exit 1; }
    php -l "$package/calltrack/api/$file" >/dev/null
done
patch --dry-run --batch --forward -d "$root" -p1 < "$package/calltrack/integration.patch" >/dev/null
echo 'Source patch is applicable. Schema, policy, signers and service sandbox still require separate approval.'
[[ "$mode" == --apply ]] || exit 0
[[ $EUID -eq 0 ]] || { echo 'Operator root context required for --apply'; exit 1; }
exec 9>/run/lock/sales-churn-calltrack-install.lock
flock -n 9 || { echo 'Another installation is active'; exit 1; }
backup=$(mktemp -d /var/backups/sales-churn-calltrack.XXXXXXXX)
files=(analytics_integration.php manager_tasks_service.php)
for file in "${files[@]}"; do cp -a -- "$root/api/$file" "$backup/$file"; done
switched=0
rollback() {
    local rc=${1:-$?}
    trap - ERR INT TERM
    if [[ $switched == 1 ]]; then
        local recovered=1
        for file in "${files[@]}"; do cp -a -- "$backup/$file" "$root/api/$file" || recovered=0; done
        # Preserve newly installed files and every backup for investigation. Disabled
        # policy and unchanged reviewed manifest block their use until an audit.
        if [[ $recovered == 1 ]]; then
            echo "Installation failed; original integration restored. Backups: $backup" >&2
        else
            echo "AUTOMATIC RESTORE FAILED. Restore original files manually from: $backup" >&2
        fi
    fi
    exit "${rc:-1}"
}
trap rollback ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM
switched=1
patch --batch --forward -d "$root" -p1 < "$package/calltrack/integration.patch" >/dev/null
for file in analytics_permanent.php analytics_permanent_cli.php; do install -o root -g root -m 0644 "$package/calltrack/api/$file" "$root/api/$file"; done
for file in "${files[@]}" analytics_permanent.php analytics_permanent_cli.php; do php -l "$root/api/$file" >/dev/null; done
trap - ERR INT TERM
echo "Code installed; backups: $backup"
echo 'No DB changes, manifest updates, service changes or enablement performed. Reviewed manifest must be approved separately.'
