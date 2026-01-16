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

### 1. `touch` (default)
Creates a marker file (`PATCHY_TOUCH.md`) to trigger a PR without modifying code.

```bash
python -m patchy.patchy_graph --service myservice --error-type npe --mode touch
```

### 2. `note`
Appends a comment/note to the target file with context about the error.

```bash
python -m patchy.patchy_graph --service myservice --error-type npe --mode note
```

### 3. `fix`
Applies defensive code based on error type. **Requires `fault_line` for best results.**

```bash
python -m patchy.patchy_graph --service myservice --error-type npe --mode fix \
  --logger "org.example.MyClass" \
  --stacktrace "NullPointerException at MyClass.java:42"
```

## Fix Strategies (Java)

| Error Type | Strategy | What It Does |
|------------|----------|--------------|
| `npe`, `null`, `nullpointer` | `npe_guard` | Inserts `Objects.requireNonNull()` |
| `duplicate`, `constraint`, `unique` | `duplicate_check` | Adds existence check TODO |
| `illegal`, `argument`, `validation` | `validation_check` | Adds parameter validation TODO |
| `persist`, `prepersist`, `save` | `duplicate_check` | Adds pre-save check TODO |
| Other | `try_catch` | Wraps in try-catch with logging |

## Usage

### Basic Usage

```bash
# Touch mode (safest, just creates a PR marker)
python -m patchy.patchy_graph --service dehnlicense --error-type npe

# Note mode (adds context comment to file)
python -m patchy.patchy_graph --service dehnlicense --error-type npe --mode note

# Fix mode (adds defensive code)
python -m patchy.patchy_graph --service dehnlicense --error-type npe --mode fix \
  --logger "org.devpoint.dehnlicense.controller.LicensePurchaseController"
```

### With Stacktrace (Recommended for Fix Mode)

```bash
python -m patchy.patchy_graph \
  --service dehnlicense \
  --error-type npe \
  --mode fix \
  --stacktrace "java.lang.NullPointerException at LicensePurchaseController.java:127"
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--service` | Service name (required, must be in `repos.json`) |
| `--error-type` | Type of error (e.g., `npe`, `duplicate`, `validation`) |
| `--mode` | `touch`, `note`, or `fix` |
| `--logger` | Java logger name to locate the file |
| `--stacktrace` | Stacktrace to extract fault line |
| `--hint` | Search hint for locating the file |
| `--jira` | Link to related Jira ticket |
| `--draft` | Create as draft PR (`true`/`false`) |

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

## Future Improvements

- [ ] Better stacktrace parsing for automatic `fault_line` extraction
- [ ] Support for Kotlin, Go, Rust
- [ ] LLM-assisted analysis for complex patterns
- [ ] Integration with test frameworks to validate fixes
