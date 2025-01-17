#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''

# dftb-uv
This file is a customization of an original script provided at the following
GitHub directory:https://github.com/radi0sus/orca_uv
The script iterates over a list of directories and in each of them performs the following steps:
(1) reads the electronic excitation UV spectrum computed from the TD-DFTB+ from the file "EXC.DAT"
(2) applies Gaussian smoothing on the spectrum
(3) optional: writes the smoothed spectrum in the file "EXC-smoothed.DAT"
(4) optional: plots the original spectrum and the smoothed spectrum in the file "abs_spectrum.png"
'''

import sys  # sys files processing
import os  # os file processing
import re  # regular expressions
import argparse  # argument parser
import numpy as np  # summation
from mpi4py import MPI
import math
import matplotlib.pyplot as plt  # plots
from matplotlib.offsetbox import (OffsetImage, AnnotationBbox)
import matplotlib.image as image
from scipy.signal import find_peaks  # peak detection

from rdkit.Chem.rdmolops import RemoveAllHs
from rdkit.Chem.rdmolfiles import MolFromPDBFile, MolToSmiles
from rdkit.Chem.Draw import rdMolDraw2D
from rdkit.Chem import AllChem

from utils import nsplit, gauss

plt.rcParams.update({'font.size': 22})

planck_constant = 4.1357 * 1e-15  # eV s
light_speed = 299792458  # m / s
meter_to_nanometer_conversion = 1e+9

# global constants
found_uv_section = True  # check for uv data in out
specstring_start = ''  # check orca.out from here
specstring_end = ''  # stop reading orca.out from here
w_eV = 0.5  # w = line width for broadening - eV, FWHM
w_nm = 10.0  # w = line width for broadening - nm, FWHM
export_delim = " "  # delimiter for data export

# parameters to discretize the spectrum
#spectrum_discretization_step = 0.2

# plot config section - configure here
nm_plot = True  # wavelength plot in nm if True, if False energy plot in eV
show_single_gauss = True  # show single gauss functions if True
show_single_gauss_area = True  # show single gauss functions - area plot if True
show_conv_spectrum = True  # show the convoluted spectra if True (if False peak labels will not be shown)
show_sticks = True  # show the stick spectra if True
label_peaks = False  # show peak labels if True
minor_ticks = True  # show minor ticks if True
show_grid = False  # show grid if True
linear_locator = False  # tick locations at the beginning and end of the spectrum x-axis, evenly spaced
spectrum_title = "Absorption spectrum"  # title
spectrum_title_weight = "bold"  # weight of the title font: 'normal' | 'bold' | 'heavy' | 'light' | 'ultrabold' | 'ultralight'
y_label = "intensity"  # label of y-axis
x_label_eV = r'energy (eV)'  # label of the x-axis - eV
x_label_nm = r'wavelength (nm)'  # label of the x-axis - nm
figure_dpi = 100  # DPI of the picture


def convert_ev_in_nm(ev_value):
    return 1 / ev_value * planck_constant * light_speed * meter_to_nanometer_conversion



comm = MPI.COMM_WORLD
comm_size = comm.Get_size()
comm_rank = comm.Get_rank()

# parse arguments
parser = argparse.ArgumentParser(prog='orca_uv', description='Easily plot absorption spectra from orca.out')

# show the matplotlib window
parser.add_argument('-s', '--show',
                    default=0, action='store_true',
                    help='show the plot window')

# do not save the png file of the spectrum
parser.add_argument('-n', '--nosave',
                    default=1, action='store_false',
                    help='do not save the spectrum')

# plot the wave number spectrum
parser.add_argument('-peV', '--ploteV',
                    default=1, action='store_false',
                    help='plot the energy spectrum')

# change line with (integer) for line broadening - nm
parser.add_argument('-wnm', '--linewidth_nm',
                    type=int,
                    default=1,
                    help='line width for broadening - wavelength in nm')

# change line with (integer) for line broadening - energy
parser.add_argument('-weV', '--linewidth_eV',
                    type=int,
                    default=10,
                    help='line width for broadening - energy in eV')

# individual x range - start
parser.add_argument('-x0', '--startx',
                    type=int,
                    help='start spectrum at x nm or eV')

# individual x range - end
parser.add_argument('-x1', '--endx',
                    type=int,
                    help='end spectrum at x nm or eV')

# export data for the line spectrum in a csv-like fashion
parser.add_argument('-e', '--export',
                    default=1, action='store_true',
                    help='export data')

# do not save the png file of 2d drawing for molecule
parser.add_argument('-md', '--mdraw',
                    default=1, action='store_false',
                    help='do not save the molecule 2d drawing')

# pare arguments
args = parser.parse_args()

# change values according to arguments
show_spectrum = args.show
save_spectrum = args.nosave
export_spectrum = args.export
save_moldraw = args.mdraw

min_energy = float('inf')
max_energy = float('-inf')


# nm_plot = args.plotwn

def find_energy_and_wavelength_extremes(path, min_energy, max_energy):
    comm.Barrier()
    if comm_rank == 0:
        print("=" * 50, flush=True)
        print("Computing minimum and maximum of the spectrum range", flush=True)
        print("=" * 50, flush=True)
    comm.Barrier()

    dirs = None
    if comm_rank == 0:
        dirs = [f.name for f in os.scandir(path) if f.is_dir()]

    dirs = comm.bcast(dirs, root=0)

    rx = list(nsplit(range(len(dirs)), comm_size))[comm_rank]
    for dir in sorted(dirs)[rx.start:rx.stop]:
        print("f Rank: ", comm_rank, " - dir: ", dir, flush=True)
        # collect information about molecular structure and chemical composition
        spectrum_file = path + '/' + dir + '/' + '/' + 'EXC.DAT'
        if os.path.exists(spectrum_file):
            energylist = list()  # energy eV
            # open a file
            # check existence
            try:
                with open(spectrum_file, "r") as input_file:
                    count_line = 0
                    for line in input_file:
                        # only recognize lines that start with number
                        # split line into 3 lists mode, energy, intensities
                        # line should start with a number
                        if 5 <= count_line <= 55:
                            if re.search("\d\s{1,}\d", line):
                                energylist.append(float(line.strip().split()[0]))
                        else:
                            pass
                        count_line = count_line + 1

                min_energy = min(min_energy, energylist[0])
                max_energy = max(max_energy, energylist[-1])

            # file not found -> exit here
            except IOError:
                print(f"'{spectrum_file}'" + " not found", flush=True)
                sys.exit(1)

    return min_energy, max_energy, convert_ev_in_nm(max_energy), convert_ev_in_nm(min_energy)


def smooth_spectrum(path, min_energy, max_energy, min_wavelength, max_wavelength):
    if nm_plot:
        spectrum_discretization_step = 0.02
        xmin_spectrum = min(0.0, min_wavelength)
        xmax_spectrum = math.ceil(max_wavelength) + spectrum_discretization_step
        #xmin_spectrum = 100
        xmax_spectrum = 750
    else:
        spectrum_discretization_step = 0.01
        xmin_spectrum = min(0.0, min_energy)
        xmax_spectrum = math.ceil(max_energy) + spectrum_discretization_step

    # global lists
    statelist = list()  # mode
    energylist = list()  # energy eV
    intenslist = list()  # fosc
    gauss_sum = list()  # list for the sum of single gaussian spectra = the convoluted spectrum

    spectrum_file = path + '/' + 'EXC.DAT'
    spectrum_file_without_extension = os.path.splitext(spectrum_file)[0]
    new_spectrum_file = spectrum_file_without_extension + "-smooth.DAT"

    # Avoids regenerating the data
    if os.path.isfile(new_spectrum_file):
        return 1

    if os.path.isfile(spectrum_file):
        with open(spectrum_file, "r") as input_file:
            count_line = 0
            for line in input_file:
                # only recognize lines that start with number
                # split line into 3 lists mode, energy, intensities
                # line should start with a number
                if 5 <= count_line <= 55:
                    if re.search("\d\s{1,}\d", line):
                        energylist.append(float(line.strip().split()[0]))
                        intenslist.append(float(line.strip().split()[1]))
                else:
                    pass
                count_line = count_line + 1

    # file not found -> exit here
    else:
        print(f"'{spectrum_file}'" + " not found", flush=True)
        sys.exit(1)

    smile_string_file = os.path.join(path,dir,'smiles.pdb')
    mol = MolFromPDBFile(smile_string_file, sanitize=False, proximityBonding=True)
    try:
        mol = RemoveAllHs(mol)
    except:
        return 0
    if os.path.isfile(smile_string_file):
        with open(smile_string_file, "r") as input_file:
            smiles_string = MolToSmiles(mol)
    else:
        print(f"'{smile_string_file}'" + " not found", flush=True)
        sys.exit(1)

    if nm_plot:
        # convert wave number to nm for nm plot
        valuelist = [convert_ev_in_nm(value) for value in energylist]
        valuelist.sort()
        w = w_nm  # use line width for nm axis
    else:
        valuelist = energylist
        w = w_eV  # use line width for eV axis

    # prepare plot
    fig, ax = plt.subplots()

    # plotrange must start at 0 for peak detection
    plt_range_x = np.arange(xmin_spectrum, xmax_spectrum, spectrum_discretization_step)

    # plot single gauss function for every frequency freq
    # generate summation of single gauss functions
    for index, wn in enumerate(valuelist):
        # single gauss function line plot
        if nm_plot and not (xmin_spectrum <= valuelist[index] <= xmax_spectrum):
            break
        if show_single_gauss:
            ax.plot(plt_range_x, gauss(intenslist[index], plt_range_x, wn, w), color="grey", alpha=0.5)
            # single gauss function filled plot
        if show_single_gauss_area:
            ax.fill_between(plt_range_x, gauss(intenslist[index], plt_range_x, wn, w), color="grey", alpha=0.5)
        # sum of gauss functions
        gauss_sum.append(gauss(intenslist[index], plt_range_x, wn, w))

    # y values of the gauss summation /cm-1
    plt_range_gauss_sum_y = np.sum(gauss_sum, axis=0)

    # find peaks scipy function, change height for level of detection
    peaks, _ = find_peaks(plt_range_gauss_sum_y, height=0)

    # plot spectra
    if show_conv_spectrum:
        try:
            mol2d_im = image.imread(f"{filename}/mol_2d_drawing.png")
            imagebox = OffsetImage(mol2d_im,zoom=1.25)
            #ab = AnnotationBbox(imagebox, (max(plt_range_x)*0.8,max(plt_range_gauss_sum_y)*0.6), frameon = True)
            ab = AnnotationBbox(imagebox, (0.955, 0.975), xycoords='axes fraction', boxcoords="offset points", pad=0, frameon=True, box_alignment=(1,1))
            ax.add_artist(ab)
            # ax.text(0.96, 0.94, f'{args.w_ev}nm', va='bottom', ha='right', transform=ax.transAxes)
        except:
            pass

    # plot sticks
    if show_sticks:
        if nm_plot:
            selected_indices = [index for index, value in enumerate(valuelist) if
                                (xmin_spectrum <= valuelist[index] <= xmax_spectrum)]
            ax.stem([valuelist[index] for index in selected_indices], [intenslist[index] for index in selected_indices],
                    linefmt="dimgrey", markerfmt=" ", basefmt=" ")
        else:
            ax.stem(valuelist, intenslist, linefmt="dimgrey", markerfmt=" ", basefmt=" ")

    # optional mark peaks - uncomment in case
    # ax.plot(peaks,plt_range_gauss_sum_y_wn[peaks],"x")

    # label peaks
    # show peak labels only if the convoluted spectrum is shown (first if)
    if show_conv_spectrum:
        if label_peaks:
            for index, txt in enumerate(peaks):
                ax.annotate(peaks[index], xy=(peaks[index], plt_range_gauss_sum_y[peaks[index]]), ha="center",
                            rotation=90, size=8,
                            xytext=(0, 5), textcoords='offset points')

    # label x axis
    if nm_plot:
        ax.set_xlabel(x_label_nm)
    else:
        ax.set_xlabel(x_label_eV)

    ax.set_ylabel(y_label)  # label y axis
    ax.set_title("Absorption spectrum " + smiles_string, fontweight=spectrum_title_weight)  # title
    ax.get_yaxis().set_ticks([])  # remove ticks from y axis
    plt.tight_layout()  # tight layout

    # show minor ticks
    if minor_ticks:
        ax.minorticks_on()

    # y-axis range - no dynamic y range
    # plt.ylim(0,max(plt_range_gauss_sum_y)+max(plt_range_gauss_sum_y)*0.1) # +10% for labels

    # tick locations at the beginning and end of the spectrum x-axis, evenly spaced
    if linear_locator:
        ax.xaxis.set_major_locator(plt.LinearLocator())

    # show grid
    if show_grid:
        ax.grid(True, which='major', axis='x', color='black', linestyle='dotted', linewidth=0.5)

    # increase figure size N times
    N = 1.5
    params = plt.gcf()
    plSize = params.get_size_inches()
    params.set_size_inches((plSize[0] * N, plSize[1] * N))

    # save the plot
    if save_spectrum:
        filename, file_extension = os.path.splitext(path)

        if nm_plot:
            #plt.xlim(60,500)
            plt.savefig(f"{filename}/abs_spectrum_nm.png", dpi=figure_dpi)
        else:
            #plt.xlim(2.50,15)
            plt.savefig(f"{filename}/abs_spectrum_eV.png", dpi=figure_dpi)

    # export data
    if export_spectrum:
        # get data from plot (window)
        plotdata = ax.lines[0]
        xdata = plotdata.get_xdata()
        ydata = plt_range_gauss_sum_y
        xlimits = plt.gca().get_xlim()
        try:
            with open(new_spectrum_file) as output_file:
                for elements in range(len(xdata)):
                    if xlimits[0] <= xdata[elements] <= xlimits[1]:
                        output_file.write(str(xdata[elements]) + export_delim + str(ydata[elements]) + '\n')
        # file not found -> exit here
        except IOError:
            print("Write error. Exit.", flush=True)
            sys.exit(1)

    # show the plot
    if show_spectrum:
        plt.show()

    plt.close(fig)
    return 1


def smooth_spectra(path, min_energy, max_energy, min_wavelength, max_wavelength):
    comm.Barrier()
    if comm_rank == 0:
        print("=" * 50, flush=True)
        print("Smooth spectra", flush=True)
        print("=" * 50, flush=True)
    comm.Barrier()
    dirs = None
    if comm_rank == 0:
        dirs = [f.name for f in os.scandir(path) if f.is_dir()]

    dirs = comm.bcast(dirs, root=0)
    rx = list(nsplit(range(len(dirs)), comm_size))[comm_rank]
    total = rx.stop - rx.start
    count = 0
    for dir in sorted(dirs)[rx.start:rx.stop]:
        count = count + 1
        print("s Rank: ", comm_rank, " - dir: ", dir, ", remaining: ", total - count, flush=True)
        # collect information about molecular structure and chemical composition
        if os.path.exists(path + '/' + dir + '/' + 'EXC.DAT'):
            smooth_spectrum(path + '/' + dir, min_energy, max_energy, min_wavelength, max_wavelength)
    return 1


def draw_2Dmols(path):
    comm.Barrier()
    if comm_rank == 0:
        print("=" * 50, flush=True)
        print("Draw molecules", flush=True)
        print("=" * 50, flush=True)
    comm.Barrier()
    dirs = None
    if comm_rank == 0:
        dirs = [f.name for f in os.scandir(path) if f.is_dir()]

    dirs = comm.bcast(dirs, root=0)
    rx = list(nsplit(range(len(dirs)), comm_size))[comm_rank]
    total = rx.stop - rx.start
    count = 0
    for dir in sorted(dirs)[rx.start:rx.stop]:
        count = count + 1
        print("s Rank: ", comm_rank, " - dir: ", dir, ", remaining: ", total - count, flush=True)
        # collect information about molecular structure and chemical composition
        if os.path.exists(path + '/' + dir + '/' + 'smiles.pdb'):
            draw_2Dmol(path + '/' + dir)


def draw_2Dmol(path):
    filename, file_extension = os.path.splitext(path)
    smile_string_file = os.path.join(path, 'smiles.pdb')
    mol_string_file = f"{filename}/mol_2d_drawing.png"
    if os.path.isfile(smile_string_file):
        if os.path.isfile(mol_string_file):
            return 1
        print(f'processing: {mol_string_file}')
        mol = MolFromPDBFile(smile_string_file, sanitize=False, proximityBonding=True)
        with open(smile_string_file, "r") as input_file:
            smiles_string = MolToSmiles(mol)
        try:
            mol = RemoveAllHs(mol)
        except:
            return 0
        AllChem.Compute2DCoords(mol)
        d = rdMolDraw2D.MolDraw2DCairo(250, 250)
        rdMolDraw2D.PrepareAndDrawMolecule(d, mol)
        d.WriteDrawingText(f"{filename}/mol_2d_drawing.png")
    else:
        print(f"'{smile_string_file}'" + " not found", flush=True)
        sys.exit(1)
    return 1


if __name__ == '__main__':
    path = './dftb_gdb9_electronic_excitation_spectrum'
    min_energy, max_energy, min_wavelength, max_wavelength = find_energy_and_wavelength_extremes(path, min_energy,
                                                                                                 max_energy)
    min_energy = comm.allreduce(min_energy, op=MPI.MIN)
    max_energy = comm.allreduce(max_energy, op=MPI.MAX)
    min_wavelength = comm.allreduce(min_wavelength, op=MPI.MIN)
    max_wavelength = comm.allreduce(max_wavelength, op=MPI.MAX)
    draw_2Dmols(path)
    smooth_spectra(path, min_energy, max_energy, min_wavelength, max_wavelength)

    print("Rank ", comm_rank, " done.", flush=True)
    comm.Barrier()
    if comm_rank == 0:
        print("Done. Exiting", flush=True)
