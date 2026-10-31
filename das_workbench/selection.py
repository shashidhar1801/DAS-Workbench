"""Shared text selection contract, independent of Qt."""
def parse_channels(text,total):
    values=[]
    for part in text.replace(" ","").split(","):
        if not part:continue
        if ":" in part:
            bits=[int(x) for x in part.split(":")]
            if len(bits) not in (2,3):raise ValueError("Use start:stop or start:stop:step; stop is excluded.")
            if bits[0]<0 or bits[1]>total or bits[1]<=bits[0] or (len(bits)==3 and bits[2]<=0):
                raise ValueError("Channel range exceeds the acquisition or has a nonpositive step.")
            values.extend(range(*bits))
        else:values.append(int(part))
    if not values or len(set(values))!=len(values) or min(values)<0 or max(values)>=total:
        raise ValueError(f"Select unique zero-based channels 0–{total-1}.")
    return tuple(values)
