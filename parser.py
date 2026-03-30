def parse_logs(content):
    steps = []

    for line in content.split("\n"):
        if "STEP" in line and "TIME" in line and "STATUS" in line:
            try:
                parts = line.split('|')

                step = parts[0].split(':')[1].strip()
                
                time_str = parts[1].split(':')[1].strip()
                time = int(''.join(filter(str.isdigit, time_str)) or 0)
                
                status = parts[2].split(':')[1].strip().upper()
                if status == 'FAILURE':
                    status = 'FAILED'

                steps.append({
                    'step': step,
                    'time': time,
                    'status': status
                })

            except Exception as e:
                print("ERROR parsing line:", line)

    print("DEBUG STEPS:", steps)  # DEBUG OUTPUT
    return steps
