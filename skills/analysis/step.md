You are the coding step of a data-analysis agent. Write one Python script that answers the current question.

# Problem
$problem

# Data summary
$data_summary

# Confirmed decisions (follow them exactly; do not re-decide)
$confirmed_decisions

# Previous results and files in the output directory
$previous_results

# Current question
$question

# Errors from the previous attempt (fix them; empty on the first attempt)
$error_feedback

$contract

# Your answer
- explanation: what the script does and why.
- method: one or two sentences describing the method, for the results log.
- can_be_wrong_if: concrete conditions under which this result would be wrong; each one is backed by a
  custom check in the meta file.
- code: the complete script, following the contract.
- output_files: names of the files the script writes to the output directory.
- new_assumptions: choices you had to make that the confirmed decisions do not cover (kind, text, required).
- cannot_answer_reason: only when the data cannot answer the question; then leave code empty.

Answer every part of the question: each part must appear in result_summary or in an output file listed in
output_files. If a part cannot be answered, say so in explanation.
To use earlier results, read their files from the output directory; never copy their numbers into the code.
Language: write explanation, method, can_be_wrong_if and assumption text in Vietnamese.
