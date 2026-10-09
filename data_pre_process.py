### This script aimed to unify all 15 runs of glyco-teflon experiment data into a single csv file

from pathlib import Path
import csv

file_prefix = "formal-run"
file_sufix = "-data.txt"
glyco_raw_data_file_name_matrix = []
water_raw_data_file_name_matrix = []

CWD = Path(__file__).resolve().parent.parent
glyco_raw_data_folder_path = CWD/"glyco_teflon_data"
water_raw_data_folder_path = CWD/"water_nylon_data"

for e in range(1,26):
    file_name = file_prefix + str(e) + file_sufix
    glyco_raw_data_file_name_matrix.append(glyco_raw_data_folder_path / file_name)

for e in range(1,28):
    file_name = file_prefix + str(e) + file_sufix
    water_raw_data_file_name_matrix.append(water_raw_data_folder_path / file_name)

def get_number_of_files(folder_name):
    folder_path = CWD/folder_name
    return sum(1 for item in folder_path.iterdir() if item.is_file())

def create_summary_data_file(type,source_folder_name,summary_file_name):
    with open(summary_file_name,"w") as sum_csv:
        writer = csv.writer(sum_csv)
        header = ["Time(sec)","Position(mm)","Trail_id"]
        writer.writerow(header)
        if type == 0:
            target_matrix = glyco_raw_data_file_name_matrix
        else:
            target_matrix = water_raw_data_file_name_matrix

        for e in range(get_number_of_files(source_folder_name)):
            index = e + 1
            with open(target_matrix[e],"r") as data_file:
                data_content = data_file.readlines()
                for itter in range(2,len(data_content)):
                    #need to split the data line into 1x2 matrix then append the group number
                    temp0 = data_content[itter].strip("\n")
                    temp = temp0.split("\t")
                    temp.append(index)
                    print(temp)
                    writer.writerow(temp)

create_summary_data_file(0,"glyco_teflon_data","glyco_summary_data.csv")
create_summary_data_file(1,"water_nylon_data","water_summary_data.csv")