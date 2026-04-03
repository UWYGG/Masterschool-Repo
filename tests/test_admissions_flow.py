"""
Integration-style test checklist for the admissions flow.

Suggested implementation order:
1) Add fixture for app/service setup.
2) Add happy path test to accepted.
3) Add IQ fail and interview fail tests to rejected.
4) Add second-chance visibility/behavior tests.
5) Add validation/error-path tests.
"""

# TODO(1): Add test: create user returns unique id.
#
# TODO(2): Add test: full happy path -> accepted.
#
# TODO(3): Add test: IQ score < 60 -> rejected.
#
# TODO(4): Add test: IQ score 60..75 enables second_chance_test.
#
# TODO(5): Add test: second chance <= 75 -> rejected.
#
# TODO(6): Add test: interview decision != passed_interview -> rejected.
#
# TODO(7): Add test: invalid step/task payload returns 400.
#
# TODO(8): Add test: unknown user returns 404.
