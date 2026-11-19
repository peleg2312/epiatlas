"""Step 4: show MRI / CT / PET / fMRI slices, with one image laid over another.

Every image is first turned to the same orientation, RAS: x goes to the patient's Right,
y to the front (Anterior), z up (Superior).
To lay a PET over an MRI, each MRI pixel is converted to millimetres in the scanner
(using the 'affine' matrix stored in every file) and then to the matching PET voxel.
This is exact for images taken in the same scanner space. Images from different days
need a registration step first (CURRY can export already-aligned images).
Display follows the radiological convention: the patient's right is on the screen's left.
"""
from functools import lru_cache

import nibabel as nib
import numpy as np
from scipy.ndimage import map_coordinates


@lru_cache(maxsize=8)  # keep recently used images in memory, so scrolling is fast
def load(path):
    image = nib.as_closest_canonical(nib.load(path))  # turn to RAS orientation
    data = np.asarray(image.dataobj, dtype=np.float32)
    if data.ndim == 4:  # fMRI or dynamic PET has a 4th dimension (time): show the average
        data = data.mean(axis=-1)
    data = np.nan_to_num(data)
    # Display range: ignore the darkest 1% and brightest 0.5% so a few extreme voxels don't wash out the image
    values = data[data != 0] if data.any() else data
    low, high = np.percentile(values, [1, 99.5])
    return data, image.affine, (float(low), float(high)), image.header.get_zooms()[:3]


def plane_voxels(shape, axis, index):
    """Voxel coordinates of every screen pixel of one slice, already flipped the way doctors view it."""
    size_x, size_y, size_z = shape
    right_to_left = np.arange(size_x)[::-1]
    front_to_back = np.arange(size_y)[::-1]
    top_to_bottom = np.arange(size_z)[::-1]
    if axis == "axial":
        rows, cols = np.meshgrid(front_to_back, right_to_left, indexing="ij")
        return np.stack([cols, rows, np.full_like(rows, index)])
    if axis == "coronal":
        rows, cols = np.meshgrid(top_to_bottom, right_to_left, indexing="ij")
        return np.stack([cols, np.full_like(rows, index), rows])
    rows, cols = np.meshgrid(top_to_bottom, front_to_back, indexing="ij")  # sagittal
    return np.stack([np.full_like(rows, index), cols, rows])


def to_grey(values, low, high):
    """Scale values to 0-255 for the screen."""
    return (np.clip((values - low) / (high - low + 1e-9), 0, 1) * 255).astype(np.uint8)


def slice_bytes(base, axis, index, overlay=None):
    """One slice of the base image, or of the overlay image resampled onto the base image's grid."""
    data, affine, (low, high), _ = load(base)
    axis_number = ["sagittal", "coronal", "axial"].index(axis)
    index = int(np.clip(index, 0, data.shape[axis_number] - 1))
    voxels = plane_voxels(data.shape, axis, index)

    if overlay is None:
        return to_grey(data[voxels[0], voxels[1], voxels[2]], low, high), index

    over_data, over_affine, (over_low, over_high), _ = load(overlay)
    points = np.vstack([voxels.reshape(3, -1), np.ones(voxels[0].size)])
    # base voxel -> millimetres in the scanner -> overlay voxel
    over_voxels = (np.linalg.inv(over_affine) @ affine @ points)[:3]
    values = map_coordinates(over_data, over_voxels, order=1, cval=np.nan).reshape(voxels.shape[1:])

    outside = np.isnan(values)  # outside the overlay's field of view
    grey = to_grey(np.nan_to_num(values, nan=over_low), over_low, over_high)
    grey[grey == 0] = 1   # 0 is kept to mean "transparent" on the screen
    grey[outside] = 0
    return grey, index


def volume_info(path):
    data, _, display_range, zooms = load(path)
    return {"shape": list(data.shape), "zooms": [float(z) for z in zooms], "range": display_range}
