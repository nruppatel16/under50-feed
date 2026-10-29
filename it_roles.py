"""
it_roles.py — what counts as an IT job, for every source in the pipeline.

Copied from the job pipeline (src/it_roles.py); keep the two in step.

One vocabulary, used by discover.py (every source, including company careers
sites) and copied into jobscout-feed/JobScout.gs and feed.py
(`python3 src/it_roles.py --js` prints the lists for the Apps Script).

A title is IT when, in order:
  1. it names none of HARD_EXCLUDE (never IT, whatever else it says), and
  2. it names a STRONG_IT word (clearly IT: software, help desk, Salesforce…), or
  3. it names an IT_KEYWORDS word and none of NON_IT (nurse, mortgage, civil…).

Many IT jobs aren't titled "IT" — implementation, onboarding, customer success,
technical account, pre-sales, field service, POS, CRM/ERP, informatics… — so
those are in the lists. Level (no senior, no co-op) is handled separately.
"""

import re

# Clearly IT on their own — keep even if a NON_IT word also appears
# ("Insurance Software Developer", "Retail Technology Analyst").
STRONG_IT = [
    "software", "developer", "programmer", "coder", "it", "i.t.", "information technology",
    "information systems", "informatics", "computer", "data", "database", "dba", "sql", "etl",
    "cloud", "aws", "azure", "gcp", "devops", "devsecops", "mlops", "sre", "site reliability",
    "kubernetes", "terraform", "linux", "unix", "sysadmin", "network", "networking", "noc",
    "cyber", "cybersecurity", "infosec", "information security", "soc analyst", "penetration", "pentest",
    "qa", "quality assurance", "sdet", "test automation", "software tester",
    "help desk", "helpdesk", "service desk", "desktop support", "technical support", "tech support",
    "it support", "application support", "end user", "euc", "deskside",
    "web developer", "front end", "frontend", "front-end", "back end", "backend", "back-end",
    "full stack", "fullstack", "full-stack", "ios", "android", "mobile developer", "mobile app",
    "machine learning", "ml", "ai", "artificial intelligence", "llm", "nlp", "computer vision",
    "analytics", "business intelligence", "bi", "power bi", "tableau",
    "salesforce", "servicenow", "sap", "oracle", "workday", "dynamics 365", "sharepoint",
    "power platform", "power apps", "rpa", "uipath", "erp", "crm", "saas", "api",
    "python", "java", "javascript", "typescript", ".net", "c#", "golang", "react", "angular", "node.js",
    "technology", "infrastructure", "systems administrator", "systems analyst",
    "implementation", "integration", "application", "applications", "firmware", "embedded", "iot", "blockchain",
    "game developer", "gameplay", "unity developer", "data centre", "data center",
    "active directory", "intune", "microsoft 365", "office 365", "m365", "google workspace",
    "technical account", "solutions engineer", "sales engineer", "pre-sales", "presales",
    "telecom", "telecommunications",
]

# IT on most titles, but also used outside IT — kept unless a NON_IT word is present.
IT_KEYWORDS = STRONG_IT + [
    "engineer", "web", "mobile", "platform", "systems", "system", "administrator", "security",
    "identity", "iam", "grc", "vulnerability", "threat", "incident response", "forensics", "privacy",
    "tester", "testing", "test", "automation", "technician", "technologist", "technical", "tech",
    "support", "customer support", "client support", "product support", "tier 1", "tier 2", "tier 3",
    "field service", "onsite support", "pos", "telecom", "telecommunications", "voip", "wireless",
    "cabling", "av technician", "audio visual", "onboarding", "customer success", "client success",
    "professional services", "solutions", "solution", "configuration", "product owner",
    "product manager", "product analyst", "scrum", "agile", "business analyst", "business systems",
    "functional consultant", "reporting", "ux", "ui", "user experience", "user interface",
    "ecommerce", "e-commerce", "webmaster", "wordpress", "shopify", "hardware", "robotics", "gis",
    "architect", "technical writer", "documentation", "developer relations", "devrel",
    "digital transformation", "endpoint", "mdm", "jira", "atlassian", "product specialist",
    "technical trainer", "operations analyst", "it coordinator", "scientist", "digital",
]

# Not IT — drops a title unless it also names a STRONG_IT word.
NON_IT = [
    "nurse", "nursing", "rn", "rpn", "paramedic", "physician", "doctor", "pharmacist", "pharmacy",
    "dental", "dentist", "hygienist", "veterinary", "physiotherapist", "therapist", "chiropractor",
    "optician", "medical laboratory", "lab technician", "laboratory", "phlebotomist", "caregiver",
    "teacher", "educator", "early childhood", "ece", "tutor", "chef", "cook", "kitchen", "food server",
    "restaurant server", "lab", "chemist", "chemistry", "biology", "biologist", "drug", "ndt",
    "electrical", "fire alarm", "fire", "flight", "aerospace", "fly-by-wire", "stability",
    "operating engineer", "hygiene", "landscape", "program", "instructional", "storytelling",
    "communications", "journalist", "broadcast", "music", "theatre",
    "bartender", "barista", "retail", "store", "merchandiser", "sales associate", "warehouse",
    "driver", "truck", "courier", "delivery", "mechanic", "automotive", "hvac", "electrician",
    "plumber", "welder", "carpenter", "millwright", "machinist", "roofer", "painter", "landscaper",
    "crane", "heavy equipment", "mechanical", "civil", "structural", "chemical", "geotechnical",
    "mining", "petroleum", "environmental", "construction", "building", "manufacturing engineer",
    "process engineer", "industrial engineer", "electrical engineer", "power engineer",
    "stationary engineer", "engineering technician", "aircraft", "avionics", "aerodynamic",
    "mortgage", "teller", "financial advisor", "financial planner", "investment advisor",
    "insurance advisor", "underwriter", "claims", "accountant", "accounting", "bookkeeper",
    "payroll", "tax", "real estate", "realtor", "leasing", "recruiter", "talent acquisition",
    "human resources", "hr generalist", "marketing", "social media", "receptionist",
    "administrative assistant", "executive assistant", "office assistant", "legal", "lawyer",
    "paralegal", "law clerk", "social worker", "counsellor", "youth worker", "pastor",
    "maintenance", "administrative support", "mailroom", "equipment operator", "trade support", "housekeeping", "food", "beauty", "cosmetic", "hair", "nail", "spa",
    "fitness", "lifeguard", "concierge", "branch", "banking advisor", "client advisor",
    "sales advisor", "beauty advisor",
]

# Never IT, whatever else the title says.
HARD_EXCLUDE = [
    "data entry", "security guard", "security officer - patrol", "loss prevention",
    "personal support worker", "psw", "support worker", "personal support", "cashier",
    "janitor", "custodian", "cleaner", "forklift", "general labour", "labourer", "laborer",
    "picker", "packer", "material handler", "dishwasher", "housekeeper", "nanny", "professor",
]

# Search terms for boards that need a query (Glassdoor, Indeed, LinkedIn, Job Bank…):
# every IT field, entry to mid level, including roles named outside "IT".
SEARCH_TERMS = [
    # software & web
    "software developer", "software engineer", "junior developer", "web developer",
    "frontend developer", "backend developer", "full stack developer", "mobile developer",
    "python developer", "java developer", ".NET developer", "javascript developer",
    "game developer", "embedded software",
    # data & AI
    "data analyst", "data engineer", "data scientist", "business intelligence", "BI developer",
    "reporting analyst", "machine learning", "AI engineer", "database administrator",
    # cloud, infrastructure, networks
    "cloud engineer", "devops", "site reliability", "systems administrator", "network administrator",
    "network engineer", "network technician", "infrastructure analyst", "linux administrator",
    "windows administrator", "data centre technician", "telecom technician",
    # security
    "cybersecurity", "security analyst", "SOC analyst", "IAM analyst", "GRC analyst",
    # support & end user
    "IT support", "help desk", "service desk", "desktop support", "technical support",
    "IT technician", "field service technician", "end user computing", "application support",
    "product support", "client support analyst", "POS support",
    # QA
    "QA analyst", "QA engineer", "software tester", "test automation",
    # business, product, implementation
    "business analyst", "IT business analyst", "systems analyst", "product owner", "scrum master",
    "implementation specialist", "implementation consultant", "onboarding specialist",
    "integration specialist", "solutions consultant", "solutions engineer",
    "technical account manager", "customer success", "sales engineer", "IT consultant",
    "IT project coordinator", "ERP analyst", "Salesforce", "ServiceNow", "SAP", "CRM administrator",
    "UX designer", "technical writer", "IT analyst", "informatics",
]


def _rx(words):
    return re.compile(
        # \w, not [a-z0-9]: accented letters are part of a word ("Préposé" isn't "pos")
        r"(?<!\w)(" + "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True)) + r")(?!\w)",
        re.I,
    )


_STRONG, _ANY, _NON, _HARD = _rx(STRONG_IT), _rx(IT_KEYWORDS), _rx(NON_IT), _rx(HARD_EXCLUDE)


def is_it_role(title: str) -> bool:
    t = title or ""
    if _HARD.search(t):
        return False
    if _STRONG.search(t):
        return True
    return bool(_ANY.search(t)) and not _NON.search(t)


if __name__ == "__main__":
    import json
    import sys
    if "--js" in sys.argv:
        for name in ("STRONG_IT", "IT_KEYWORDS", "NON_IT", "HARD_EXCLUDE", "SEARCH_TERMS"):
            print(f"const {name} = {json.dumps(sorted(set(globals()[name]), key=str.lower) if name != 'SEARCH_TERMS' else globals()[name])};")
    else:
        for t in sys.argv[1:]:
            print("IT " if is_it_role(t) else "-- ", t)
