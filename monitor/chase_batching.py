"""Shared range batching; old fixed-size profiles retain their exact behavior."""
def batch_limits(config):
    low=config.get('batch_size_min');high=config.get('batch_size_max')
    if low is None and high is None:low=high=config.get('batch_size',3)
    if type(low) is not int or type(high) is not int or not 1<=low<=high<=20:raise ValueError('每批素材数量区间无效')
    return low,high

def groups(items,config):
    """Use as many items as possible with the fewest allowed whole batches."""
    low,high=batch_limits(config);n=len(items);count=(n+high-1)//high
    if count*low>n:count=n//low
    remaining=min(n,count*high);offset=0
    for index in range(count):
        size=min(high,remaining-low*(count-index-1))
        yield items[offset:offset+size]
        offset+=size;remaining-=size
