"""Model router: decides which model tier a queued task needs.

The decision is deterministic and explainable. It does not look for a keyword
and pick a model; it builds a picture of the task from several kinds of
evidence and scores it:

  * intent, per clause: what is being done (create, fix, explain, rename ...)
    and to what (documentation, code, configuration)
  * technical signals with a strength, ignored when they are negated
    ("without touching the schema")
  * structure: files and modules named, work items, acceptance criteria,
    stack traces, questions and hedges
  * the workspace: whether the files named exist and what kind they are

From that it derives eleven dimension scores (complexity, reasoning depth,
affected modules, change size, architecture, database, security, testing,
ambiguity, regression risk, effort), a 0-100 complexity score, and a tier:

  haiku   only with positive evidence that the change is trivial
  opus    when a critical signal is present or the score is high
  sonnet  everything else

A short prompt is never treated as an easy one: without evidence of a trivial
change the floor is sonnet.
"""
from __future__ import annotations

import os
import re

from . import router_ar as _ar

VERSION = "4"   # 2: Arabic and mixed Arabic/English requests are analysed as well
# 3: "migrate/move/switch the database to <engine>" is recognised as a database migration
# 4: "replica" alone is not database replication (read replicas, replica sets, replication are)
TIERS = ("haiku", "sonnet", "opus")            # ascending capability
POLICIES = ("balanced", "quality_first", "cost_optimized")

DIMENSIONS = ("technical_complexity", "reasoning_depth", "affected_modules", "code_changes",
              "architecture_impact", "database", "security", "testing", "ambiguity",
              "regression_risk", "effort")

_WEIGHTS = {"technical_complexity": 1.6, "reasoning_depth": 1.6, "affected_modules": 0.9,
            "code_changes": 0.9, "architecture_impact": 1.3, "database": 0.9, "security": 0.9,
            "testing": 0.4, "ambiguity": 0.4, "regression_risk": 1.0, "effort": 0.7}
# A task never maxes every dimension; this weighted sum already means "as hard as it gets".
_FULL_SCALE = 48.0

# score thresholds per policy: (haiku_max, opus_min)
_THRESHOLDS = {"balanced": (16, 62), "quality_first": (10, 48), "cost_optimized": (22, 78)}


def _table(*rows):
    return [(label, strength, re.compile(pattern, re.IGNORECASE)) for label, strength, pattern in rows]


# Technical signals: (label, strength 1-3, pattern). Strength 3 is decisive on its own.
SIGNALS = {
    "hard_debug": _table(
        ("root cause", 3, r"\broot[- ]cause"),
        ("intermittent failure", 3, r"\b(intermittent|flaky|flakiness|sporadic|random(ly)?|sometimes|occasionally|now and then|once in a while)\b"),
        ("cannot reproduce", 3, r"\b(can'?t|cannot|unable to|hard to|difficult to) reproduce"),
        ("memory leak", 3, r"\bmemory leak|\bleak(s|ing)? memory|\bout of memory\b|\boom\b"),
        ("race condition", 3, r"\b(race condition|data race|races? with)\b"),
        ("deadlock", 3, r"\bdead ?lock"),
        ("data corruption", 3, r"\bcorrupt(s|ed|ion)?\b"),
        ("hang", 2, r"\b(hangs?|hanging|freez(es|ing)|frozen|gets? stuck|infinite loop)\b"),
        ("crash dump", 3, r"\b(segfault|segmentation fault|core dump|crash dump|access violation)\b"),
        ("environment-specific failure", 3, r"\b(only|just) (happens |occurs |fails |breaks )?(in|on) (prod|production|staging|ci)\b|\bworks (locally|on my machine) but\b|\bbut not (locally|in dev)\b"),
        ("unexplained behaviour", 2, r"\bwhy (does|do|is|are|did|would)\b|\bno idea why\b|\bfor no (apparent )?reason\b|\bunexplained\b|\bmysterious(ly)?\b"),
        ("investigation", 2, r"\b(investigate|diagnos(e|is|ing)|troubleshoot|track down|figure out why|find out why|get to the bottom)\b"),
        ("failure under load", 2, r"\bunder (heavy |high )?load\b|\bat scale\b|\bwhen busy\b"),
    ),
    "concurrency": _table(
        ("threading", 2, r"\b(multi[- ]?thread\w*|thread[- ]safe\w*|thread safety|threads?|threading)\b"),
        ("concurrency", 3, r"\bconcurren\w+|\bparallel(ism|ize|ise)?\b"),
        ("synchronisation", 2, r"\b(mutex|semaphore|locking|lock contention|critical section|atomic(ity)?)\b"),
        ("deadlock", 3, r"\bdead ?lock"),
        ("race", 3, r"\brace condition|\bdata race"),
    ),
    "architecture": _table(
        ("architecture", 3, r"\barchitect(ure|ural|ing)?\b|\bre-?architect"),
        ("system design", 3, r"\b(re)?design(ing)? (the |a |an |our |new )*(system|schema|data ?model|database|module structure|plugin system|public api|api|platform|framework|pipeline|infrastructure|service|domain)"),
        ("redesign", 3, r"\bredesign|\bre-?structur(e|ing) the (whole|entire|application|project|codebase)"),
        ("migration to another stack", 3, r"\b(migrate|migrating|migration|port|porting|move|moving|upgrade|upgrading|convert|converting)\b[^.;\n]{0,40}\b(to|onto|from)\b[^.;\n]{0,25}\b(\.net( core)?|net ?\d|react|vue|angular|typescript|python ?3|microservices?|kubernetes|aws|azure|gcp|graphql|grpc|blazor|next\.?js|framework|platform|oauth2?|oidc|sso|saml)"),
        ("service decomposition", 3, r"\b(microservices?|monolith|service[- ]oriented|event[- ]driven|event sourcing|cqrs|message[- ]driven)\b"),
        ("multi-tenancy", 3, r"\bmulti[- ]?tenan"),
        ("decoupling", 2, r"\b(decoupl\w+|modulari[sz]\w+|abstraction layer|plugin system|extension points?|dependency inversion|layered|hexagonal|clean architecture|domain[- ]driven)\b"),
        ("scalability", 2, r"\bscalab\w+|\bhorizontal(ly)? scal"),
        ("cross-cutting concern", 2, r"\bcross[- ]cutting\b|\bfoundation for\b|\bfrom scratch\b"),
        ("breaking interface change", 2, r"\bbreaking change|\bpublic (api|interface|contract)\b"),
    ),
    "database": _table(
        ("schema change", 3, r"\b(schema|data ?model) (change|design|redesign|migration|update)s?\b|\b(alter|redesign|normali[sz]e|denormali[sz]e|split|merge)\b[^.;\n]{0,30}\b(tables?|schema|columns?)\b"),
        ("database migration", 3, r"\b(db|database|schema|data|ef|entity framework|sql|alembic|flyway|liquibase)[- ]?migrations?\b|\bmigrations?\b[^.;\n]{0,30}\b(table|column|schema|database|db|sql|index)|\bbackfill|\b(migrat\w*|mov(e|ing)|switch(ing)?|port(ing)?)\b[^.;\n]{0,20}\b(db|database|data ?store)\b[^.;\n]{0,20}\b(to|onto|from)\b[^.;\n]{0,15}\b(postgre\w*|mysql|maria ?db|sql ?server|mssql|oracle|mongo\w*|sqlite|dynamo ?db|cassandra|cockroach\w*|aurora|cosmos ?db|another|a new|a different)\b"),
        ("transactions", 3, r"\btransactions?\b|\btransactional\b|\bisolation level|\brollback segment|\bunit of work\b"),
        ("database design", 3, r"\bdatabase (design|architecture|structure)\b|\bdata ?model(l?ing)?\b|\bentity relationship|\bpartition(ing|ed)?\b|\bshard(ing|ed)?\b|\breplication\b|\bread[- ]replicas?\b|\breplica ?sets?\b|\b(database|db|sql|postgres\w*|mysql|mongo\w*|redis|cluster)\b[^.;\n]{0,30}\breplicas?\b|\breplicas?\b[^.;\n]{0,20}\b(of the |for the )?(database|db)\b"),
        ("schema", 2, r"\bschema\b"),
        ("indexes and constraints", 2, r"\b(foreign key|primary key|unique constraint|constraints?|composite index|db index|database index)\b|\bindex(es)? (on|for|to) \b|\badd (an? )?index"),
        ("stored logic", 2, r"\bstored proc(edure)?s?\b|\bdatabase triggers?\b|\bsql functions?\b"),
        ("database access", 1, r"\b(sql|database|db|orm|entity framework|ef core|dapper|hibernate|sequelize|prisma|sqlalchemy|queries|query|repository|repositories)\b"),
    ),
    "security": _table(
        ("authentication protocol", 3, r"\b(oauth2?|oidc|openid|saml|sso|single sign[- ]on|kerberos|ldap|active directory)\b"),
        ("access control design", 3, r"\b(access control|rbac|abac|role[- ]based|permission (system|model|scheme)|authori[sz]ation (model|architecture|system|scheme|layer)|authentication (architecture|system|flow|layer))\b"),
        ("cryptography", 3, r"\b(encrypt\w*|decrypt\w*|cryptograph\w*|hash(ing)? (the )?passwords?|password hashing|key rotation|certificate|tls|signing keys?)\b"),
        ("vulnerability", 3, r"\b(vulnerab\w+|exploit\w*|cve-?\d*|sql injection|xss|cross[- ]site|csrf|injection attack|privilege escalation|security (hole|flaw|issue|bug|fix|review|audit))\b"),
        ("multi-factor authentication", 3, r"\b(2fa|mfa|two[- ]factor|multi[- ]factor|totp)\b"),
        ("authentication", 2, r"\bauthenticat\w+|\bauthori[sz]\w+|\bauth\b|\bidentity\b"),
        ("credentials", 2, r"\b(jwt|tokens?|passwords?|credentials?|secrets?|api keys?|session (management|fixation|hijack\w*)|impersonat\w+)\b"),
        ("roles and permissions", 2, r"\b(roles?|permissions?|privileges?)\b"),
        ("login", 1, r"\b(log ?in|sign ?in|log ?out|sign ?up|logout|signup)\b"),
        ("input sanitising", 1, r"\bsaniti[sz]\w+|\bescap(e|ing) (user )?input"),
    ),
    "performance": _table(
        ("bottleneck", 3, r"\bbottleneck|\bn\s?\+\s?1\b|\bperformance (regression|degradation|problem|issue)s?\b"),
        ("profiling", 2, r"\bprofil(e|ing|er)\b|\bbenchmark\w*|\bload test\w*"),
        ("slowness", 2, r"\b(slow|slowly|slower|slowness|sluggish|laggy|takes? (too )?long|takes? \d+ ?(s|sec|seconds|minutes))\b"),
        ("optimisation", 2, r"\boptimi[sz]\w+|\bperformance\b|\bperformant\b|\bfaster\b|\bspeed up\b|\bthroughput\b|\blatency\b"),
        ("resource usage", 2, r"\b(high|excessive|reduce) (cpu|memory|ram|disk|io)\b|\b(cpu|memory) usage\b"),
        ("caching", 1, r"\bcach(e|ing)\b"),
    ),
    "integration": _table(
        ("integration failure", 3, r"\bintegration (failure|failures|error|errors|issue|issues|problem|problems|breaks?|broken)\b"),
        ("distributed system", 3, r"\bdistributed\b|\beventual consistency|\bidempoten\w+|\bexactly[- ]once|\bsaga\b"),
        ("messaging", 2, r"\b(message (queue|broker|bus)|kafka|rabbitmq|service bus|sqs|pub ?/ ?sub|event bus)\b"),
        ("payments", 2, r"\b(payment (gateway|provider|processor)|stripe|paypal|billing provider)\b"),
        ("data synchronisation", 2, r"\bsync(hroni[sz]\w+)? (between|across|with)\b|\bout of sync\b|\breconcil\w+"),
        ("external API", 1, r"\b(third[- ]party|external) (api|service|system)s?\b|\bwebhooks?\b|\bgrpc\b|\bsoap\b|\bapi contract"),
    ),
    "production": _table(
        ("production system", 3, r"\b(production|in prod|on prod|live (site|system|environment|data|database)|mission[- ]critical|high[- ]risk)\b"),
        ("zero downtime", 3, r"\b(zero|no|without)[- ]downtime\b"),
        ("data loss", 3, r"\bdata loss|\blos(e|ing) data\b"),
        ("incident", 3, r"\b(outage|incident|customers? (are |is |were )?(affected|impacted|complain\w*)|release blocker)\b"),
        ("backward compatibility", 2, r"\bbackwards?[- ]compat\w+|\bwithout breaking\b|\bmust not break\b|\bexisting (clients|users|customers|data)\b"),
        ("rollback", 2, r"\broll ?back\b|\bhotfix\b"),
    ),
    "refactor": _table(
        ("rewrite", 3, r"\brewrit(e|ing)\b|\bre-?implement"),
        ("refactoring", 2, r"\brefactor\w*|\bre-?structur\w+|\bmoderni[sz]\w+|\btechnical debt\b|\blegacy\b"),
        ("consolidation", 1, r"\b(clean ?up|simplif\w+|consolidat\w+|de-?duplicat\w+|remove duplication|extract (a |an |the )?(method|function|class|component|module|service|helper))\b"),
    ),
}

# What kind of work a clause describes.
_DOC_TARGET = re.compile(
    r"\b(readme|change ?log|contributing|documentation|docs?|wiki|guide|tutorial|manual|faq|"
    r"release notes?|comments?|docstrings?|javadoc|typos?|spelling|grammar|wording|phrasing|"
    r"sections?|chapters?|paragraphs?|headings?|table of contents|how-?to|"
    r"install(ation)? (steps|instructions|guide)|troubleshooting (section|guide|tips|page)|"
    r"licen[sc]e (text|file|header)|copyright (notice|year|header))\b|\.(md|rst|txt|adoc)\b",
    re.IGNORECASE)
_TEXT_CHANGE = re.compile(
    r"\b(typos?|spelling|grammar|wording|phrasing|misspell\w*|capitali[sz]\w+|punctuation)\b|"
    r"\b(change|update|fix|reword|rename|correct|translate)\b[^.;\n]{0,25}\b(text|label|caption|"
    r"title|heading|headline|copy|tooltip|placeholder|message|string)\b", re.IGNORECASE)
_EXPLAIN = re.compile(
    r"\b(explain|describe|summari[sz]e|document|outline|list|clarify|write (a |an |the )?(short |brief )?"
    r"(note|summary|explanation|description|overview))\b|\bwhat (does|do|is)\b|\bhow (does|do)\b",
    re.IGNORECASE)
_MECHANICAL = re.compile(
    r"\b(rename|reformat|re-?indent|auto-?format|code format(ting)?|format (the )?(code|files?)|"
    r"alphabeti[sz]e|sort (the )?(imports|usings|list|"
    r"entries|keys)|reorder|re-?organi[sz]e (the )?(files?|folders?|director\w+|imports)|organi[sz]e "
    r"(the )?(files?|folders?|director\w+)|move (the )?files?|bump (the )?version|update (the )?"
    r"(copyright|year|version number)|remove (the )?(unused|dead|obsolete|commented[- ]out) "
    r"(imports?|usings?|code|files?|variables?)|whitespace|line endings|trailing (spaces|whitespace)|"
    r"find and replace|search and replace|add (licen[sc]e )?headers?|delete (the )?(old|unused|temp\w*|"
    r"backup) files?)\b", re.IGNORECASE)
_CONFIG = re.compile(
    r"\b(config(uration)?( file| value| option| setting)?s?|settings?( file)?|\.env|environment "
    r"variables?|feature flags?|appsettings|web\.config)\b|\.(ya?ml|toml|ini|json|config|env)\b",
    re.IGNORECASE)
_CODE_TARGET = re.compile(
    r"\b(function|method|class|classes|module|component|controller|service|endpoint|api|route|"
    r"handler|middleware|model|view|page|screen|form|button|modal|dialog|table|grid|dropdown|menu|"
    r"layout|query|queries|schema|migration|database|test|tests|feature|validation|logic|algorithm|"
    r"parser|worker|job|scheduler|queue|cache|session|payment|checkout|search|filter|pagination|"
    r"dashboard|report|chart|export|import|upload|download|notification|email|pipeline|build|"
    r"deployment|dockerfile|script|command|cli|library|package|dependency|interface|type|enum|"
    r"exception|bug|crash|error handling|behaviou?r|workflow|integration|webhook|ui|ux|frontend|"
    r"backend|server|client|app|application|code|codebase|implementation)s?\b", re.IGNORECASE)
_CREATE_VERB = re.compile(
    r"\b(implement|add|build|create|develop|introduce|support|enable|extend|wire up|hook up|"
    r"integrate|generate|scaffold|set up|make)\b", re.IGNORECASE)
_FIX_NOUN = re.compile(
    r"\b(bugs?|broken|crash(es|ing|ed)?|exceptions?|stack ?trace|fails?|failing|failure|"
    r"regression|incorrect(ly)?|wrong(ly)?|doesn'?t work|does not work|not working|unexpected|"
    r"error(s)?(?! messages?| text| strings?| codes?| pages?| handling))\b", re.IGNORECASE)
_FIX_VERB = re.compile(r"\b(fix|repair|resolve|patch|correct|debug|solve)\b", re.IGNORECASE)
_UI = re.compile(
    r"\b(ui|ux|front[- ]?end|page|view|screen|form|button|modal|dialog|grid|dropdown|layout|css|"
    r"styl(e|es|ing)|responsive|component|dashboard|chart|search box|filter(s|ing)?|pagination|"
    r"sort(ing)?|razor|cshtml|html|react|vue|angular|blazor|mvc|theme|dark mode|navbar|sidebar|"
    r"wizard|tooltip|accessibility)\b", re.IGNORECASE)
_BACKEND = re.compile(
    r"\b(api|endpoint|controller|service|handler|route|middleware|repository|business logic|rest|"
    r"graphql|dto|asp\.net|django|express|spring|laravel|rails|flask|web api|crud|background job|"
    r"scheduler|validation)\b", re.IGNORECASE)
_TEST_WORK = re.compile(
    r"\b(unit|integration|e2e|end[- ]to[- ]end|regression|functional|ui|smoke) tests?\b|"
    r"\b(add|write|create|extend|improve) (more |some |the |missing )?(tests?|test cases|test "
    r"coverage|coverage)\b|\b(xunit|nunit|mstest|jest|pytest|mocha|junit|vitest|playwright|cypress)\b",
    re.IGNORECASE)
_TESTS_REQUIRED = re.compile(
    r"\b(tests? (must |should |still |all )*pass(es|ing)?|all tests|test suite|coverage|no "
    r"regressions?|ci (is |stays |must be )?green|build (passes|succeeds))\b", re.IGNORECASE)
_BREADTH = re.compile(
    r"\b(all|every|each|multiple|several|many|various) (of )?(the |our )?(modules?|services?|"
    r"projects?|controllers?|components?|pages?|endpoints?|layers?|packages?|repositories|repos|"
    r"apps?|applications?|places|files|classes|tables|features|teams|tenants)\b|"
    r"\b(entire|whole|across the|throughout|codebase[- ]wide|project[- ]wide|system[- ]wide|"
    r"application[- ]wide|end[- ]to[- ]end|everywhere|global(ly)?|multi[- ]module)\b", re.IGNORECASE)
_NARROW = re.compile(
    r"\b(only|just|single|one[- ]line|small|minor|tiny|quick|simple|trivial|slight(ly)?|"
    r"straightforward|little|cosmetic)\b", re.IGNORECASE)
_SHARED = re.compile(
    r"\b(core|shared|common|base (class|classes)|foundation|framework|kernel|public api|"
    r"used (by|in) (all|every|many)|widely used|central)\b", re.IGNORECASE)
_HEDGE = re.compile(
    r"\b(maybe|perhaps|possibly|somehow|not sure|unsure|unclear|figure out|find out|look into|"
    r"whatever|best (way|approach|option)|decide|choose between|or alternatively|if possible|"
    r"something like|some kind of|sort of|tbd|to be decided|should we|evaluate|explore|research|"
    r"consider (whether|if)|or whatever|i think|i guess|probably)\b", re.IGNORECASE)
_NEGATION = re.compile(
    r"\b(no|not|without|don'?t|do not|doesn'?t|does not|never|avoid|except|excluding|unrelated to|"
    r"other than|instead of|rather than|leave|keep)\b[^.;\n]{0,40}$", re.IGNORECASE)
_ACTION = re.compile(
    r"\b(add|implement|create|build|update|fix|change|remove|delete|refactor|migrate|write|move|"
    r"rename|replace|convert|design|redesign|investigate|optimi[sz]e|test|document|configure|"
    r"integrate|extend|support|handle|validate|rewrite|split|merge|upgrade|deploy|debug|expose|"
    r"wire|introduce|ensure|make|extract|clean|generate|enable|disable|improve)\b", re.IGNORECASE)
_FILE = re.compile(
    r"(?<![\w/\\.-])((?:[\w.-]+[/\\])*[\w.-]+\.(?:cs|cshtml|razor|vb|fs|py|js|jsx|ts|tsx|java|kt|go|"
    r"rb|php|rs|c|cc|cpp|h|hpp|swift|m|sql|html?|css|scss|less|vue|svelte|json|ya?ml|xml|toml|ini|"
    r"config|csproj|sln|gradle|sh|ps1|bat|cmd|md|rst|txt|adoc|env|lock|tf|proto))(?![\w-])",
    re.IGNORECASE)
_IDENTIFIER = re.compile(r"\b(?:[A-Z][a-z0-9]+){2,}\b|\b[a-z]+(?:_[a-z0-9]+){1,}\b|`[^`\n]{2,60}`")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.MULTILINE)
_STACK = re.compile(r"```|Traceback \(most recent|^\s+at [\w.$<>]+\(|Exception:|\bstack ?trace\b",
                    re.MULTILINE | re.IGNORECASE)
_CLAUSE_SPLIT = re.compile(r"[.;:]+(?=\s|$)|\n+|\s+(?:and then|then|and also|as well as|plus)\s+|,\s*(?:and\s+)?(?=(?:"
                           r"add|implement|create|build|update|fix|change|remove|refactor|migrate|write|"
                           r"move|rename|replace|convert|design|investigate|optimi[sz]e|document)\b)|"
                           r"\s+and\s+(?=(?:add|implement|create|build|update|fix|change|remove|refactor|"
                           r"migrate|write|move|rename|replace|convert|design|investigate|optimi[sz]e|"
                           r"document)\b)", re.IGNORECASE)

_DOC_EXT = {"md", "rst", "txt", "adoc"}
_CONFIG_EXT = {"json", "yml", "yaml", "xml", "toml", "ini", "config", "env", "lock", "csproj", "sln",
               "gradle"}
_DB_EXT = {"sql"}


def _clamp(value, low=0.0, high=10.0):
    return max(low, min(high, value))


def _strengths(text):
    """Strength (0-4) and matched labels per signal category, skipping negated mentions."""
    result = {}
    for category, table in SIGNALS.items():
        best, strong, labels = 0, 0, []
        for label, strength, pattern in table:
            for match in pattern.finditer(text):
                if _NEGATION.search(text[max(0, match.start() - 90):match.start()]):
                    continue
                labels.append(label)
                best = max(best, strength)
                strong += 1 if strength >= 2 else 0
                break
        level = min(4, best + (1 if strong >= 2 else 0))
        result[category] = (level, labels)
    return result


def _clauses(text):
    return [part.strip() for part in _CLAUSE_SPLIT.split(text) if part and part.strip()]


# Verbs that mean real engineering work even when the clause names no familiar code noun.
_WORK_VERB = re.compile(
    r"\b(refactor\w*|rewrit\w+|redesign\w*|re-?architect\w*|restructur\w+|migrat\w+|"
    r"optimi[sz]\w+|investigat\w+|diagnos\w+|debug\w*|upgrad\w+|integrat\w+|"
    r"harden\w*|paralleli[sz]\w+|deploy\w*|moderni[sz]\w+|decoupl\w+|re-?implement\w*)\b",
    re.IGNORECASE)
_NEGATIVE_CLAUSE = re.compile(
    r"^\s*(?:and\s+|but\s+)?(?:no|not|don'?t|do not|never|without|avoid|leave|keep|nothing)\b",
    re.IGNORECASE)


# Writing a design, a plan or a comparison is reasoning work even though the output is prose.
_DESIGN_DOC = re.compile(
    r"\b(design doc(ument)?s?|technical (design|spec(ification)?)|proposal|rfc|adr|architecture "
    r"decision|(migration|rollout|rollback|implementation) plan|strategy|roadmap|trade-?offs?|"
    r"compare (the )?(options|approaches|alternatives)|evaluate (the )?(options|approaches|"
    r"alternatives)|recommend (an? |the )?(approach|solution|design))\b", re.IGNORECASE)


def _clause_kind(clause, context=None):
    """What one clause asks for: doc, text, explain, mechanical, config, design, fix, create,
    code or None. `context` is the kind of the preceding clause: "fix the headings" after
    "update the README" is still documentation work."""
    if _NEGATIVE_CLAUSE.search(clause):
        return None  # "do not touch the auth code" describes what is out of scope
    if _DESIGN_DOC.search(clause):
        return "design"
    doc = _DOC_TARGET.search(clause)
    code = _CODE_TARGET.search(clause)
    if _TEXT_CHANGE.search(clause) and not _FIX_NOUN.search(clause):
        return "text"
    if doc and (code is None or doc.start() < code.start()):
        return "doc"
    if _MECHANICAL.search(clause):
        return "mechanical"
    if _FIX_NOUN.search(clause):
        return "fix"
    if _FIX_VERB.search(clause) and not doc:
        return "doc" if context in ("doc", "text") and not code else "fix"
    if _EXPLAIN.search(clause) and not _CREATE_VERB.search(clause) and not _FIX_VERB.search(clause):
        return "explain"
    if _CREATE_VERB.search(clause) and (code or _UI.search(clause) or _BACKEND.search(clause)):
        return "create"
    if _WORK_VERB.search(clause):
        return "code"
    if _CONFIG.search(clause) and not code:
        return "config"
    if code or _UI.search(clause) or _BACKEND.search(clause):
        if context in ("doc", "text", "explain") and not _ACTION.search(clause):
            return context
        return "code"
    if doc:
        return "doc"
    return None


def _files(text, project_root):
    """Files named in the prompt, classified by type; checked against the workspace when given."""
    found = {}
    for match in _FILE.finditer(text):
        name = match.group(1).replace("\\", "/")
        if name.lower() in found:
            continue
        extension = name.rsplit(".", 1)[-1].lower()
        kind = ("doc" if extension in _DOC_EXT else "config" if extension in _CONFIG_EXT
                else "db" if extension in _DB_EXT else "code")
        size = None
        if project_root:
            try:
                size = os.path.getsize(os.path.join(project_root, name))
            except OSError:
                size = None
        found[name.lower()] = {"name": name, "kind": kind, "size": size}
        if len(found) >= 25:
            break
    return list(found.values())


def analyze(prompt, acceptance=None, project_root=None):
    """Extract everything the scoring uses. Pure function of its inputs (plus file sizes)."""
    # Analysis works on a copy with Arabic spelling variants unified; what is stored and
    # sent to Claude is always the original text.
    prompt = _ar.normalize(prompt or "")
    acceptance = _ar.normalize(acceptance or "")
    text = prompt if acceptance and acceptance in prompt else (prompt + "\n" + acceptance).strip()
    strengths = _strengths(text)
    level = {name: value[0] for name, value in strengths.items()}

    light = {"doc", "text", "explain", "mechanical", "config"}
    kinds, context = [], None
    for clause in _clauses(text):
        kind = _clause_kind(clause, context)
        if kind is None and not _NEGATIVE_CLAUSE.search(clause) \
                and any(value[0] >= 2 for value in _strengths(clause).values()):
            # No recognisable wording, but it names something technically heavy: that is
            # work, and it must not disappear behind a documentation clause next to it.
            kind = "code"
        if kind:
            kinds.append(kind)
            context = kind
    light_only = bool(kinds) and all(kind in light for kind in kinds)
    # In a request that only asks for documentation or an explanation, the technical terms
    # are the subject being written about, not work to be done ("document the auth API").
    # Signs of a real investigation still count: "explain why it randomly fails" is debugging.
    topics = {}
    if light_only and level["hard_debug"] < 2:
        topics = {name: value for name, value in level.items() if value}
        level = {name: 0 for name in level}
    files = _files(text, project_root)
    code_files = [item for item in files if item["kind"] in ("code", "db")]
    doc_files = [item for item in files if item["kind"] == "doc"]
    identifiers = {m.group(0).strip("`") for m in _IDENTIFIER.finditer(prompt)}

    words = len(re.findall(r"\S+", text))
    bullets = len(_BULLET.findall(text))
    actions = len({m.group(0).lower() for m in _ACTION.finditer(text)})
    work_items = max(bullets, min(actions, 8), 1)
    acceptance_items = len([line for line in acceptance.splitlines() if line.strip()]) if acceptance else 0
    breadth = min(2, len({m.group(0).lower() for m in _BREADTH.finditer(text)}))
    narrow = bool(_NARROW.search(text))
    hedges = len({m.group(0).lower() for m in _HEDGE.finditer(text)})
    questions = text.count("?") + text.count("\u061f")
    modules = len({os.path.dirname(item["name"]) for item in code_files if "/" in item["name"]})
    large_file = any((item["size"] or 0) > 40000 for item in code_files)

    hard = {name: level[name] for name in ("hard_debug", "concurrency", "architecture", "database",
                                           "security", "performance", "integration", "production",
                                           "refactor")}
    # Work that changes how code behaves, as opposed to text, layout of files, or explanations.
    test_work = bool(_TEST_WORK.search(text)) and not light_only
    behaviour = (any(kind in ("fix", "create", "code", "design") for kind in kinds)
                 or test_work
                 or any(value >= 2 for value in hard.values())
                 or (not kinds and bool(code_files)))
    return {
        "text": text, "words": words, "kinds": kinds, "level": level,
        "labels": {name: value[1] for name, value in strengths.items()},
        "files": files, "code_files": len(code_files), "doc_files": len(doc_files),
        "modules": modules, "identifiers": len(identifiers), "work_items": work_items,
        "bullets": bullets, "acceptance_items": acceptance_items, "breadth": breadth,
        "narrow": narrow, "hedges": hedges, "questions": questions,
        "stack_trace": bool(_STACK.search(text)), "large_file": large_file,
        "shared": bool(_SHARED.search(text)), "ui": bool(_UI.search(text)),
        "backend": bool(_BACKEND.search(text)), "test_work": test_work, "topics": topics,
        "tests_required": bool(_TESTS_REQUIRED.search(text)),
        "behaviour": behaviour, "light_only": light_only,
    }


def dimensions(facts):
    """The eleven 0-10 dimension scores."""
    level = facts["level"]
    code = 1.0 if facts["behaviour"] else 0.0
    light = facts["light_only"] and not facts["behaviour"]
    files = facts["code_files"]
    spread = max(files, facts["modules"] * 2, min(facts["identifiers"], 6) // 2)

    ambiguity = _clamp(1.5 * facts["hedges"] + 1.5 * min(facts["questions"], 3)
                       + (2.0 if code and facts["words"] < 9 and not files else 0.0)
                       + (2.0 if not facts["kinds"] and not any(level.values()) else 0.0))
    technical = _clamp(2.0 * code + (0.5 if light and facts["kinds"] else 0.0)
                       + 1.6 * level["hard_debug"] + 1.5 * level["concurrency"]
                       + 1.0 * level["performance"] + 1.0 * level["integration"]
                       + 1.0 * level["architecture"] + 0.6 * level["refactor"]
                       + 0.5 * max(0, level["database"] - 1) + 0.5 * max(0, level["security"] - 1))
    reasoning = _clamp(1.0 * code + 2.0 * level["hard_debug"] + 1.6 * level["architecture"]
                       + 1.0 * level["concurrency"] + 0.9 * level["performance"]
                       + 0.5 * level["integration"] + 0.3 * ambiguity
                       + (1.0 if facts["stack_trace"] else 0.0))
    modules = _clamp((1.0 if code else 0.0) + (0 if spread <= 1 else 2 if spread <= 3 else 4 if spread <= 6 else 7)
                     + 2.5 * facts["breadth"] - (1.0 if facts["narrow"] and not facts["breadth"] else 0.0))
    items = facts["work_items"]
    changes = _clamp((1.0 if light else 2.0 if code else 0.5)
                     + (0 if items <= 1 else 2 if items <= 3 else 4 if items <= 6 else 6)
                     + 1.5 * facts["breadth"] + (1.0 if facts["words"] > 150 else 0.0)
                     + (1.0 if facts["large_file"] else 0.0)
                     - (1.0 if facts["narrow"] and not facts["breadth"] else 0.0))
    architecture = _clamp(2.5 * level["architecture"]
                          + (2.0 if level["refactor"] >= 2 and facts["breadth"] else 0.0)
                          + (1.5 if level["database"] >= 3 else 0.0)
                          + (1.5 if level["security"] >= 3 else 0.0)
                          + (1.0 if facts["shared"] and code else 0.0))
    database = float((0, 2, 5, 8, 10)[level["database"]])
    security = float((0, 1, 5, 8, 10)[level["security"]])
    testing = _clamp(3.0 * facts["test_work"] + min(4, facts["acceptance_items"])
                     + (2.0 if facts["tests_required"] else 0.0))
    risk = _clamp(1.0 * code + 2.5 * level["production"] + 1.5 * facts["breadth"]
                  + 0.8 * level["refactor"] + (1.0 if level["database"] >= 2 else 0.0)
                  + (1.0 if level["security"] >= 2 else 0.0) + (1.0 if facts["shared"] and code else 0.0))
    effort = _clamp(0.45 * changes + 0.3 * modules + 0.15 * testing
                    + (1.0 if facts["words"] > 80 else 0.0) + 0.1 * reasoning)
    return {"technical_complexity": round(technical, 1), "reasoning_depth": round(reasoning, 1),
            "affected_modules": round(modules, 1), "code_changes": round(changes, 1),
            "architecture_impact": round(architecture, 1), "database": database,
            "security": security, "testing": round(testing, 1), "ambiguity": round(ambiguity, 1),
            "regression_risk": round(risk, 1), "effort": round(effort, 1)}


def critical_signals(facts, dims):
    """Reasons a task needs the strongest model. Each is (description, strength 2-4)."""
    level, labels = facts["level"], facts["labels"]
    found = []

    def add(text, strength, category=None):
        detail = ", ".join(labels.get(category, [])[:2]) if category else ""
        found.append(("%s (%s)" % (text, detail) if detail else text, strength))

    if level["hard_debug"] >= 3:
        add("difficult debugging and root-cause analysis", level["hard_debug"], "hard_debug")
    if level["concurrency"] >= 3:
        add("concurrency", level["concurrency"], "concurrency")
    if level["architecture"] >= 3:
        add("architecture work", level["architecture"], "architecture")
    if level["database"] >= 3:
        add("database design or migration", level["database"], "database")
    if level["security"] >= 3:
        add("authentication, authorization or security design", level["security"], "security")
    if level["performance"] >= 3 or (level["performance"] >= 2 and level["hard_debug"] >= 2):
        add("performance bottleneck needing investigation", max(3, level["performance"]), "performance")
    if level["integration"] >= 3 or (level["integration"] >= 2 and level["hard_debug"] >= 2):
        add("complex integration failure", max(3, level["integration"]), "integration")
    if level["refactor"] >= 3 or (level["refactor"] >= 2 and (facts["breadth"] or dims["affected_modules"] >= 6)):
        add("complex refactoring across the codebase", 3, "refactor")
    if dims["affected_modules"] >= 7 and facts["behaviour"]:
        add("changes across many modules", 3)
    if level["production"] >= 3 and facts["behaviour"] and (
            level["database"] >= 2 or level["security"] >= 2 or facts["breadth"]
            or level["hard_debug"] >= 2 or level["refactor"] >= 2):
        add("high-risk production change", level["production"], "production")
    return found


def _score(dims):
    total = sum(_WEIGHTS[name] * dims[name] for name in DIMENSIONS)
    return int(round(min(100.0, 100.0 * total / _FULL_SCALE)))


def _work_description(facts):
    """Short phrase describing ordinary (sonnet-level) work."""
    kinds, level = facts["kinds"], facts["level"]
    parts = []
    if "fix" in kinds:
        parts.append("routine bug fix")
    if facts["test_work"]:
        parts.append("tests")
    if level["refactor"] >= 1:
        parts.append("standard refactoring")
    if "design" in kinds:
        parts.append("design or planning work")
    if "create" in kinds or (not parts and facts["behaviour"]):
        area = "frontend" if facts["ui"] and not facts["backend"] else \
            "backend" if facts["backend"] and not facts["ui"] else \
            "frontend and backend" if facts["ui"] and facts["backend"] else ""
        parts.insert(0, "standard %s feature implementation" % area if area
                     else "standard feature implementation")
    extras = [name for name in ("database", "security", "performance", "integration")
              if level[name] >= 1]
    text = ", ".join(parts) or "general development task"
    if extras:
        text += " touching " + ", ".join(extras)
    return text[0].upper() + text[1:]


def _light_description(facts):
    kinds = set(facts["kinds"])
    if kinds <= {"doc"}:
        return "Simple documentation change"
    if kinds <= {"text", "doc"}:
        return "Small text change"
    if kinds <= {"explain", "doc", "config"}:
        return "Simple explanation or documentation"
    if "mechanical" in kinds:
        return "Straightforward mechanical change"
    return "Low-complexity maintenance"


def classify(prompt, acceptance=None, policy="balanced", project_root=None):
    """Route one task. Returns the tier, the reasoning and every number behind it."""
    if policy not in POLICIES:
        policy = "balanced"
    facts = analyze(prompt, acceptance, project_root)
    dims = dimensions(facts)
    score = _score(dims)
    critical = critical_signals(facts, dims)
    haiku_max, opus_min = _THRESHOLDS[policy]
    level = facts["level"]

    # Haiku needs positive evidence of a trivial change and no evidence of risk.
    blockers = []
    if facts["behaviour"]:
        blockers.append("changes code behaviour")
    if not facts["kinds"]:
        blockers.append("no clear low-risk intent")
    elif not facts["light_only"]:
        blockers.append("includes development work")
    if facts["breadth"] and facts["code_files"]:
        blockers.append("broad scope")
    if critical:
        blockers.append("critical signals")
    if facts["work_items"] > 6 or facts["words"] > 220:
        blockers.append("large request")
    haiku_eligible = not blockers

    strongest = max([strength for _, strength in critical] or [0])
    total_critical = sum(strength for _, strength in critical)
    substantial = (level["refactor"] >= 2 or level["database"] >= 2 or level["security"] >= 2
                   or level["architecture"] >= 2 or level["production"] >= 2
                   or level["hard_debug"] >= 2)
    if policy == "quality_first":
        wants_opus = bool(critical) or score >= opus_min or (substantial and score >= 34)
    elif policy == "cost_optimized":
        wants_opus = (strongest >= 4 or total_critical >= 6 or score >= opus_min
                      or (strongest >= 3 and score >= 52))
    else:
        wants_opus = strongest >= 3 or score >= opus_min

    if haiku_eligible and score <= haiku_max:
        tier = "haiku"
        reason = _light_description(facts)
    elif wants_opus:
        tier = "opus"
        if critical:
            ranked = sorted(critical, key=lambda item: -item[1])
            reason = "Complex task: " + "; ".join(text for text, _ in ranked[:3])
        else:
            reason = "Large, high-complexity task (%s)" % _work_description(facts).lower()
    else:
        tier = "sonnet"
        if haiku_eligible:
            reason = "%s, but too large for the smallest model" % _light_description(facts)
        elif critical:
            reason = ("%s; %s is handled without the top model under the cost-optimized policy"
                      % (_work_description(facts), critical[0][0]))
        else:
            reason = _work_description(facts)

    # How sure is the deterministic result? Borderline cases can be reviewed by a model.
    doubts = []
    if tier == "haiku" and score > haiku_max - 3:
        doubts.append("close to the sonnet threshold")
    if tier == "sonnet" and not critical and score >= opus_min - 8:
        doubts.append("close to the opus threshold")
    if tier == "sonnet" and critical:
        doubts.append("critical signal below the opus bar for this policy")
    if tier == "opus" and strongest < 3 and score < opus_min + 6:
        doubts.append("opus by score only, near the threshold")
    if tier == "sonnet" and not facts["kinds"]:
        doubts.append("the request gives little to go on")
    if tier == "sonnet" and facts["words"] < 7 and not critical:
        doubts.append("very short request")
    if tier == "haiku" and facts["code_files"]:
        doubts.append("light change that names code files")
    if tier == "sonnet" and not critical and _ar.has_arabic(facts["text"]):
        # Measured on prompts written after the rules were fixed: hard tasks phrased in
        # unfamiliar Arabic are the main thing the rules miss, always towards sonnet.
        doubts.append("written in Arabic, where the rules recognise fewer ways of describing "
                      "hard work")
    confidence = "high" if not doubts else "medium" if len(doubts) == 1 else "low"

    return {
        "tier": tier, "reason": reason, "score": score, "policy": policy, "version": VERSION,
        "dimensions": dims, "critical": [text for text, _ in critical],
        "haiku_eligible": haiku_eligible, "haiku_blockers": blockers,
        "confidence": confidence, "doubts": doubts,
        "kinds": sorted(set(facts["kinds"])),
        "signals": {name: facts["labels"][name] for name in facts["labels"] if facts["labels"][name]},
        "facts": {"words": facts["words"], "work_items": facts["work_items"],
                  "code_files": facts["code_files"], "doc_files": facts["doc_files"],
                  "modules": facts["modules"], "acceptance_items": facts["acceptance_items"],
                  "breadth": facts["breadth"], "stack_trace": facts["stack_trace"]},
    }


# --------------------------------------------------------------------------- model-assisted review

REVIEW_SYSTEM_PROMPT = (
    "You triage software development tasks for a coding agent. Pick the smallest model tier that "
    "can complete the task reliably. haiku: documentation, comments, small text changes, simple "
    "file organisation, simple explanations, straightforward repetitive edits. sonnet: normal "
    "feature work, UI, APIs, forms and validation, tests, routine bug fixes, standard "
    "refactoring. opus: architecture, difficult debugging and root-cause analysis, database "
    "design and migrations, authentication and authorization design, complex or cross-module "
    "refactoring, deep performance investigation, high-risk production changes. A short request "
    "is not automatically an easy one. Answer with exactly one line and nothing else, in the "
    "form tier=NAME; reason=TEXT where NAME is haiku, sonnet or opus and TEXT is at most 12 "
    "words.")

_REVIEW_RE = re.compile(r"tier\s*[=:]\s*(haiku|sonnet|opus)\b(?:\s*[;,]\s*reason\s*[=:]\s*(.*))?",
                        re.IGNORECASE)


def review_prompt(prompt, acceptance, decision):
    """The compact request sent to the reviewing model (the task text is truncated)."""
    task = (prompt or "").strip()
    if len(task) > 1800:
        task = task[:1800] + "\n[...truncated...]"
    lines = ["Task:", task]
    if acceptance and acceptance not in (prompt or ""):
        lines += ["", "Acceptance criteria:", acceptance.strip()[:500]]
    lines += ["", "A rule-based pre-classification suggested '%s' (complexity %d/100): %s."
              % (decision["tier"], decision["score"], decision["reason"]),
              "Confirm or correct it."]
    return "\n".join(lines) + "\n"


def parse_review(text):
    """Extract (tier, reason) from the reviewing model's answer, or (None, None)."""
    match = None
    for match in _REVIEW_RE.finditer(text or ""):
        pass
    if match is None:
        return None, None
    return match.group(1).lower(), (match.group(2) or "").strip().strip("'\"`.")[:120]


def merge_review(decision, reviewed_tier, reviewed_reason):
    """Combine the rule-based decision with a model's opinion, within safe bounds.

    The model may move the tier by one step. It can never send a task to haiku
    that the rules found ineligible, because a short prompt can hide a hard change.
    """
    if reviewed_tier not in TIERS or reviewed_tier == decision["tier"]:
        return decision["tier"], None
    current, proposed = TIERS.index(decision["tier"]), TIERS.index(reviewed_tier)
    step = current + (1 if proposed > current else -1)
    tier = TIERS[step]
    if tier == "haiku" and not decision["haiku_eligible"]:
        return decision["tier"], None
    note = "model review suggested %s" % reviewed_tier
    if reviewed_reason:
        note += ": %s" % reviewed_reason
    return tier, note


# --------------------------------------------------------------------------- Arabic vocabulary
# The same concepts in Modern Standard and Egyptian Arabic (see router_ar). Merging them
# into the patterns above means one analysis handles English, Arabic and mixed requests.

def _with(pattern, extra):
    return re.compile("(?:%s)|(?:%s)" % (pattern.pattern, extra), pattern.flags)


_DOC_TARGET = _with(_DOC_TARGET, _ar.DOC_TARGET)
_TEXT_CHANGE = _with(_TEXT_CHANGE, _ar.TEXT_CHANGE)
_EXPLAIN = _with(_EXPLAIN, _ar.EXPLAIN)
_MECHANICAL = _with(_MECHANICAL, _ar.MECHANICAL)
_CODE_TARGET = _with(_CODE_TARGET, _ar.CODE_TARGET)
_CREATE_VERB = _with(_CREATE_VERB, _ar.CREATE_VERB)
_FIX_NOUN = _with(_FIX_NOUN, _ar.FIX_NOUN)
_FIX_VERB = _with(_FIX_VERB, _ar.FIX_VERB)
_WORK_VERB = _with(_WORK_VERB, _ar.WORK_VERB)
_DESIGN_DOC = _with(_DESIGN_DOC, _ar.DESIGN_DOC)
_UI = _with(_UI, _ar.UI)
_BACKEND = _with(_BACKEND, _ar.BACKEND)
_TEST_WORK = _with(_TEST_WORK, _ar.TEST_WORK)
_TESTS_REQUIRED = _with(_TESTS_REQUIRED, _ar.TESTS_REQUIRED)
_BREADTH = _with(_BREADTH, _ar.BREADTH)
_NARROW = _with(_NARROW, _ar.NARROW)
_HEDGE = _with(_HEDGE, _ar.HEDGE)
_ACTION = _with(_ACTION, _ar.ACTION)
_NEGATION = _with(_NEGATION, _ar.NEGATION)
_NEGATIVE_CLAUSE = _with(_NEGATIVE_CLAUSE, _ar.NEGATIVE_CLAUSE)
_CLAUSE_SPLIT = _with(_CLAUSE_SPLIT, _ar.CLAUSE_SPLIT)
for _category, _rows in _ar.SIGNALS.items():
    SIGNALS[_category] = SIGNALS[_category] + _table(*_rows)
