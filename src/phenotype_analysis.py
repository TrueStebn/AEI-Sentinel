"""Explicit prototype marker eligibility; flag negatives remain in denominators."""

MARKER_SPECIES = {
    "ESBL": ("Escherichia coli", "Klebsiella pneumoniae"),
    "MRSA": ("Staphylococcus aureus",),
    "VRE": ("Enterococcus faecalis", "Enterococcus faecium"),
    "CPE": ("Citrobacter freundii", "Escherichia coli", "Klebsiella oxytoca", "Klebsiella pneumoniae", "Proteus mirabilis"),
}


class PhenotypeEngine:
    """Share cohort selection/reporting with advanced S/R, using flag/all-first rates."""
    def __init__(self, settings=None):
        from src.advanced_analysis import AdvancedAnalysisEngine
        self.engine = AdvancedAnalysisEngine(settings)

    def analyze(self, cohort, marker, **kwargs):
        return self.engine.analyze(cohort, mode="phenotype", code=marker, **kwargs)
