# Patchy - Automated PR Bot for Error Stabilization

Patchy is an automated "paramedic" bot that creates draft PRs to stabilize production errors. It does NOT fix bugs - it adds defensive code to prevent crashes while developers investigate the root cause.

## Philosophy: Paramedic, Not Surgeon

**Patchy stabilizes, it doesn't cure.**

| What Patchy Does | What Patchy Does NOT Do |
|------------------|------------------------|
| Add null checks to prevent NPE crashes | Fix business logic errors |
| Add validation to catch bad inputs early | Determine correct business rules |
| Add try-catch with logging for observability | Understand application intent |
| Create tickets with context for developers | Replace human code review |

### Why This Approach?

To truly "fix" a bug, you need to know the **intent** of the code. For example:

```java
// Bug: age check fails
if (user.getAge() > 18) { ... }
```

Should it be `>= 18`? Or `>= 21`? Or `> 16`? **Only the business knows.**

Patchy can add a null check for `user`, but it cannot determine the correct age threshold.

## Modes

### 1. `auto` (default)
Tries to apply a fix first. If the fix cannot be applied (no valid fault_line, unsupported pattern), falls back to adding a note.

```bash
# Let Patchy decide - tries fix, falls back to note
python -m patchy.patchy_graph --service myservice --error-type npe --jira TICKET-123
```

### 2. `fix`
Only attempts to apply defensive code. Fails if it cannot find a safe insertion point.

```bash
python -m patchy.patchy_graph --service myservice --error-type npe --mode fix \
  --stacktrace "NullPointerException at MyClass.java:42"
```

### 3. `note`
Only adds a comment/note to the target file with context about the error.

```bash
python -m patchy.patchy_graph --service myservice --error-type npe --mode note
```

## Auto Mode Flow

```
┌─────────────────────────────────────────────────────────┐
│  ¿Has valid fault_line from stacktrace?                 │
│                                                         │
│     YES ─────────────────────► Try FIX                  │
│                                      │                  │
│                              Fix successful?            │
│                                │         │              │
│                               YES       NO              │
│                                │         │              │
│                                ▼         ▼              │
│                           PR with     Apply NOTE        │
│                           code fix    (fallback)        │
│                                                         │
│     NO ──────────────────────► Apply NOTE directly      │
└─────────────────────────────────────────────────────────┘
```

## Fix Strategies (Java)

| Error Type | Strategy | What It Does |
|------------|----------|--------------|
| `npe`, `null`, `nullpointer` | `npe_guard` | Inserts `Objects.requireNonNull()` with TODO |
| `duplicate`, `constraint`, `unique` | `duplicate_check` | Adds existence check TODO |
| `illegal`, `argument`, `validation` | `validation_check` | Adds parameter validation TODO |
| `optimistic`, `locking`, `concurrent` | `try_catch` | Adds retry logic suggestion |
| `persist`, `prepersist`, `save` | `duplicate_check` | Adds pre-save check TODO |
| Other | Default | Tries npe_guard, then try_catch |

## Usage

### Basic Usage (Auto Mode)

```bash
# Patchy decides: tries fix, falls back to note if needed
python -m patchy.patchy_graph --service dehnlicense --error-type npe --jira DDSIT-163

# With stacktrace for better fix targeting
python -m patchy.patchy_graph --service dehnlicense --error-type npe --jira DDSIT-163 \
  --stacktrace "java.lang.NullPointerException at LicensePurchaseController.java:42"

# With logger name to locate the file
python -m patchy.patchy_graph --service dehnlicense --error-type npe --jira DDSIT-163 \
  --logger "org.devpoint.dehnlicense.controller.LicensePurchaseController"
```

### Explicit Mode

```bash
# Force fix mode only (fails if can't apply)
python -m patchy.patchy_graph --service dehnlicense --error-type npe --mode fix \
  --stacktrace "NullPointerException at LicensePurchaseController.java:42"

# Force note mode only
python -m patchy.patchy_graph --service dehnlicense --error-type optimistic-locking --mode note
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--service` | Service name (required, must be in `repos.json`) |
| `--error-type` | Type of error (e.g., `npe`, `duplicate`, `validation`, `optimistic-locking`) |
| `--mode` | `auto` (default), `fix`, or `note` |
| `--logger` | Java logger name to locate the file |
| `--stacktrace` | Stacktrace to extract fault line |
| `--hint` | Search hint for locating the file |
| `--jira` | Jira ticket key (e.g., `DDSIT-163`) |
| `--draft` | Create as draft PR (`true`/`false`) |

## Example Output

When fix is applied:
```java
// TODO(Patchy): Defensive null guard - investigate root cause | See DDSIT-163
java.util.Objects.requireNonNull(service, "service must not be null");
```

When note is applied (auto fallback):
```java
/*
 * Patchy note
 * Service: dehnlicense
 * Error-Type: optimistic-locking
 * Target: LicenseUsageController.java
 * Jira: DDSIT-164
 * Note: Auto-fix could not be applied; manual review needed
 */
```

## Configuration

Services are configured in `repos.json`:

```json
{
  "myservice": {
    "repo": "https://github.com/org/repo.git",
    "branch": "main",
    "src_root": "src/main/java"
  }
}
```

## Integration with Dogcatcher

Patchy can be automatically invoked after ticket creation:

```bash
python main.py --dry-run --patchy --service myservice
```

This will:
1. Fetch error logs from Datadog
2. Create Jira tickets for new errors
3. Invoke Patchy to create stabilization PRs

## Limitations

1. **No business logic fixes** - Patchy adds defensive code, not logic corrections
2. **Requires context** - Fix mode works best with `fault_line` from stacktrace
3. **Language support** - Currently optimized for Java, basic support for Python/JS/TS
4. **Safe by design** - Will skip changes if it can't find a safe insertion point

## Safety Features

- **Allowlist**: Only configured services can be modified
- **Draft PRs**: Changes go through code review
- **Duplicate detection**: Won't create duplicate branches/PRs
- **Safe insertion**: Won't insert code in file headers or invalid locations
- **Audit logging**: All actions are logged for traceability
- **Auto fallback**: If fix fails, automatically falls back to note mode
