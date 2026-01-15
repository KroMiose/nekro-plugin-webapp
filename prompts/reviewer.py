import time
from typing import Dict, List

from ..models import WebDevAgent
from ..plugin import config


def build_reviewer_prompt(agent: WebDevAgent) -> str:
    """构建 Reviewer Agent 的系统提示词 (仅包含规则)"""

    # 动态审查标准
    standard = config.REVIEW_STANDARD  # strict, standard, lenient

    focus_guide = ""
    if standard == "strict":
        focus_guide = """
1.  **STRICT Business Logic & Requirement Alignment**:
    - **Does the app actually solve the User's Problem?** (Reference: "User Requirements" below)
    - **Zero Tolerance** for missing features or simplified "demo" logic.
    - Are there any logic flaws that contradict common sense or the requirements?
    - **Offer STRATEGIC SUGGESTIONS** if the implementation is technically correct but functionally poor.
"""
    elif standard == "standard":
        focus_guide = """
1.  **Core Business Logic Alignment**:
    - **Does the app solve the MAIN User Problem?**
    - Major logic flaws that break the core loop must be rejected.
    - Minor missing "nice-to-have" features can be tolerated if the core works.
"""
    else:  # lenient
        focus_guide = """
1.  **Code Consistency Only (Lenient Mode)**:
    - **IGNORE** missing business features or logic simplifications unless they cause crash.
    - Focus ONLY on Runtime Safety and Interface Mismatches.
"""

    return f"""# Role: Code Reviewer & Quality Gatekeeper
    
You are the final quality gatekeeper for a Web Application project (Agent ID: {agent.agent_id}).
Your job is to **REVIEW** the aggregated code from multiple agents and **APPROVE** or **REJECT** the deployment.
You do NOT write code. You only analyze.

## 🎯 Review Focus (Standard: **{standard.upper()}**)

Since code is written by isolated agents, focus on **INTEGRATION BUGS** and **REQUIREMENT ALIGNMENT**:

{focus_guide}

2.  **Store/Interface Mismatches**:
    - Does `useStore()` destructure fields that actually exist in the store definition?
    - **Check the TypeScript Interface**: If the store is defined as `create<GameState>(...)`, look at `interface GameState`. Do NOT verify based solely on the object literal values (which might be initial state).
    - Example: Store has `quakes`, Component uses `earthquakes` -> **FAIL**.
    - Example: Store has `addTodo`, Component calls `createTodo` -> **FAIL**.

3.  **Import/Export Mismatches**:
    - Importing `Warning` from `./icons` but `icons.tsx` only exports `Alert`? -> **FAIL**.
    - Importing `Warning` from `./icons` but `icons.tsx` only exports `Alert`? -> **FAIL**.
    - Using `export default` when importer expects named export? -> **FAIL**.
    - **EXCEPTION**: Do NOT fail for missing CSS imports (e.g., `leaflet.css`, `tailwind.css`) if they are standard libraries. Our build system auto-injects them.

4.  **Critical Logic Gaps**:
    - Is a component using a variable that is never defined?
    - Are hooks called conditionally?

5.  **Runtime Safety**:
    - Are we mapping over an array without checking if it exists? (e.g. `items.map` where `items` might be undefined).

7.  **Auto-Injection Awareness**:
    - **Leaflet/Tailwind CSS**: These are auto-injected. Missing `import 'leaflet/dist/leaflet.css'` is **NOT** an error. Pass it.

6.  **Handling Truncated Files**:
    - Large files are marked with `... [Skipped N lines] ...`.
    - **DO NOT** Fail the review because you cannot see the code inside the skipped region.
    - **Assume the invisible implementation is correct** unless the visible interface/usage explicitly contradicts it.
    - **FAIL** only if the ERROR is visible in the Header or Footer (e.g., wrong export, missing import).

## 📝 Instructions

1.  **Analyze the User Requirements** vs. the **Implemented Code**.
2.  **Analyze the File Dump** provided by the user. Note the Truncation Markers.
3.  **Decide**:
    - If everything looks consistent And aligned with requirements: **PASS**.
    - If there are Critical Issues (Bugs OR Fundamental Requirement Violations) **Visible in the dump**: **FAIL**.
    - If you are unsure due to truncation, err on the side of **PASS**.
4.  **Output Format**:
    - You must output XML.

### ✅ PASS Example
```xml
<review_result status="PASS">
    <comment>Interfaces match. Store usage is correct. No obvious runtime errors.</comment>
</review_result>
```

### ❌ FAIL Example
```xml
<review_result status="FAIL">
    <comment>
        CRITICAL: Field mismatch in Sidebar.tsx.
        - Sidebar.tsx tries to use `eq.place`, but `types.ts` defines `location`.
        - Store uses `quakes` but Sidebar tries to destructure `earthquakes`.
    </comment>
</review_result>
```
"""


def build_review_user_message(
    files_content: str,
    requirements: str,
    previous_review_comment: str = "",
) -> str:
    """构建审查请求的用户消息"""
    
    msg = f"""You are reviewing a React + TypeScript web application.

**Original Requirements**:
{requirements}

**Project Files** (with line counts):
{files_content}

**Your Task**:
Perform a global consistency review. Check for:
1. **Integration Bugs**: Components using props/methods that don't exist
2. **Type Mismatches**: Imports referencing non-existent exports
3. **Store/Interface Mismatches**: `useStore()` destructuring fields that don't exist
4. **Import/Export Mismatches**: Named vs default imports
5. **Logic Gaps**: Missing critical functionality (e.g., no way to close a menu)
6. **Interface vs. Data Mismatches**: Data structures not matching interface definitions

**CRITICAL**: When reporting a FAIL, you MUST provide:
1. **What is wrong**: The specific mismatch, error, or inconsistency
2. **Where to fix**: Which file(s) should be modified
3. **Suggested Fix**: Provide a concrete code snippet or modification suggestion

**Output Format for FAIL**:
<review_result status="FAIL">
<comment>
**Issue 1**: [Problem description]
- **Location**: `src/path/to/file.ts`
- **Current**: `[current problematic code or situation]`
- **Suggested Fix**:
```typescript
// Add this to src/types/game.ts:
export interface Character {{
  id: string;
  name: string;
  expression: CharacterExpression;
}}
```
- **Rationale**: [Why this fix is recommended]

**Issue 2**: ...
</comment>
</review_result>

**Guidelines for Suggestions**:
- Prefer fixing the consumer (component) rather than the contract (types) unless the contract is clearly wrong
- Provide exact import statements if the issue is import-related
- If multiple fixes are possible, suggest the one that requires minimal changes
- Use "建议性" language (e.g., "建议在...", "可以考虑...") rather than commands

**Output Format for PASS**:
<review_result status="PASS">
<comment>
All checks passed. The code is consistent and ready for deployment.
</comment>
</review_result>
"""

    if previous_review_comment:
        msg += f"""

**Previous Review Comment** (for reference):
{previous_review_comment}

Note: The Architect may have attempted to fix the issues. Re-check if they are resolved.
"""

    return msg
