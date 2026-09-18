# Home Purchase Model

A tool for figuring out whether buying a specific home makes financial
sense compared to renting, under different market and mortgage scenarios.

## Getting started

1. Install the one required dependency:

   ```
   pip install pyyaml
   ```

2. Provide your inputs. You have two options:

   - Open `home_model_config.yaml` and edit the values directly (purchase
     price, down payment, income, rent, mortgage rate, etc.), or
   - Run `python3 home_model.py` and answer the interactive prompts — this
     will also save your answers into `home_model_config.yaml` for next
     time.

   Either way, `home_model_config.yaml` must contain your real numbers
   before you run the model for a result you can trust.

3. Run the model:

   ```
   python3 home_model.py
   ```

   This reads `home_model_config.yaml` and writes `model_output.json`,
   which contains all the computed results. Re-run this any time you
   change your inputs.

## Viewing your report

The model itself only produces raw numbers in `model_output.json` — it
does not generate a report on its own. To get a readable report:

1. Open a conversation with Claude (Claude Code or claude.ai) in this
   project.
2. Give Claude `model_output.json` (the fresh one from your latest run)
   along with `REPORT_TEMPLATE.md`, and ask it to publish the report.
3. Claude will generate a formatted report walking through your monthly
   costs, affordability, buy-vs-rent breakeven by scenario, and down
   payment analysis.

If you change your inputs, re-run the model (step 3 above) and generate
a fresh report from the new `model_output.json` — a report is only valid
for the inputs that produced it.
