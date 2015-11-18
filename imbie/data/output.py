import numpy as np
import csv
from itertools import izip

def save_data(data, fpath):
    """
    saves the ice sheet's data to the specified file.
    """
    with open(fpath, 'wb') as f:
        wr = csv.writer(f)

        for line in tabulate(data, headers=True):
            wr.writerow(line)

def tabulate(data, headers=False):
        """
        returns an iterator of the ice sheet's data. This
        method is used by the save_data method for outputting
        to CSV files.
        """
        if headers:
            header = []
            for name, var in data.items():
                item = var[0]
                flat = np.ravel(item)
                if len(flat) > 1:
                    for i, _ in enumerate(flat):
                        header.append(name+'_'+str(i))
                else:
                    header.append(name)
            yield header
        for line in izip(*data.values()):
            line = np.concatenate([np.ravel(item) for item in line])
            yield line