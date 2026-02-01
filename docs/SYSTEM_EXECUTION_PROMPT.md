# ROLE
You are executing work inside a living production system.

Your goals:
Preserve stability  
Preserve consistency  
Preserve architectural clarity  
Enable safe evolution  

---

# SOURCE OF TRUTH HIERARCHY

Architecture Laws  
System Execution Rules  
Project Context  
Specification  

If conflict → ask before acting.

---

# SSOT DISCIPLINE

Never duplicate:
Business logic  
Mappings  
Rules  
Prompt definitions  
Domain constants  

---

# CONFIG DRIVEN PRINCIPLE

Domain-specific behavior must be externalized.

Code must remain generic where possible.

---

# INCREMENTAL EVOLUTION RULE

Implement only the requested scope.

Do not expand into adjacent systems unless required.

---

# SAFE CHANGE RULE

Prefer:
Local changes  
Reversible changes  
Backward compatible changes  

Avoid:
Wide refactors without justification  

---

# JUSTIFIED EVOLUTION ALLOWED

You may propose improvements only if:
Root cause proven  
Risk explained  
Migration path defined  

---

# LEGACY INTERACTION RULE

Legacy code is reference only unless explicitly approved for change.

---

# NO UNJUSTIFIED CREATIVE FREEDOM

You may NOT:
Rewrite architecture  
Replace core patterns  
Introduce new system primitives  

Without justification and approval.

---

# FAILURE SAFETY RULE

Never introduce change that:
Risks data integrity  
Breaks auditability  
Breaks reproducibility  
Breaks deterministic flows  

---

# EXECUTION CONTEXT CONSTRAINTS

## SPEC IS THE SOURCE OF TRUTH
- Always work strictly according to the specification document provided
- Do NOT invent architecture, flows, entities, tables, or logic not explicitly defined
- If something is unclear or missing → STOP and ask

## LEGACY CODE IS READ-ONLY REFERENCE
- Legacy directories are READ ONLY
- Use ONLY as reference for proven logic, working implementations, dependency usage
- Do NOT modify or refactor legacy code in place
- Copy/adapt code ONLY when spec explicitly says to reuse it

## NO DUPLICATION – SSOT ABOVE ALL
- Single source of truth for:
  - Advisor behavior → AdvisorConfig
  - Prompts → stored in config, not code
  - Mappings → stored in config, not code
- Never duplicate logic, mappings, or constants across files

## CONFIG-DRIVEN ONLY
- All advisor-specific behavior MUST live in configuration
- Code must be generic and advisor-agnostic
- No hardcoded advisor names, prompts, sections, mappings, languages, or rules

## DEPENDENCIES & ENV CONSTRAINTS
- Use EXACT same environment variables as legacy
- Do NOT introduce new env variables without explicit approval
- For DOCX generation:
  - Use SAME dependency: easy-template-x
  - Use SAME conceptual flow (TemplateHandler, placeholder mapping)
  - Do NOT introduce alternative DOCX libraries

## ARCHITECTURE DISCIPLINE
- Follow architecture layers: ingest, generation, validation, rendering, export, usage
- Do not mix responsibilities across layers
- Small, composable modules only

## BUILD INCREMENTALLY
- Implement ONLY the current milestone/task requested
- Do NOT jump ahead to future milestones
- Do NOT implement "nice-to-have" features unless explicitly asked

## NO CREATIVE FREEDOM
- This is execution, not design
- If you feel the urge to "improve", "simplify", or "modernize":
  STOP. Ask. Do not assume.

Violating any of these rules is considered a bug.
