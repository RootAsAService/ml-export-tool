import mercantile
from rio_tiler import main
import numpy as np
from affine import Affine
import rasterio
from rasterio.profiles import DefaultGTiffProfile
from ml_export import tile_generator
from ml_export.tile_class_generator import TileClassDataset
import logging
from tqdm import tqdm
from torch.utils.data import DataLoader

logging.basicConfig(format='%(levelname)s:%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p', level=logging.INFO)


def calculate_webmercator_meters_per_pixel(zoom_level):
    """Calculate webmercator pixel size based on zoom level"""

    meters_per_pixel = 20037508 * 2 / 2 ** (8 + zoom_level)

    return meters_per_pixel


def calculate_zoom_tile_transform(zoom_level, tile_object, tile_creation_buffer=250):
    """Return Transform for GTiff"""

    # Calculate Pixel Size Based on desired end tile_level

    meters_per_pixel = calculate_webmercator_meters_per_pixel(zoom_level)

    # Calculate XY BBox in meters of tile_object
    bbox_xy = mercantile.xy_bounds(tile_object)

    transform = Affine(meters_per_pixel, 0, bbox_xy.left, 0,
                       -meters_per_pixel, bbox_xy.top)
    height = int((bbox_xy.top - bbox_xy.bottom) / meters_per_pixel) + tile_creation_buffer
    width = int((bbox_xy.right - bbox_xy.left) / meters_per_pixel) + tile_creation_buffer

    print(height)
    print(width)

    return transform, width, height


def create_webmercator_cog_profile(tile_object, zoom_level, num_channels, dtype=np.uint8, tile_creation_buffer=250):
    """create webmercator cog_profile for output"""

    transform, width, height = calculate_zoom_tile_transform(zoom_level=zoom_level, tile_object=tile_object)

    cog_profile = DefaultGTiffProfile(count=num_channels,
                                      height=height + tile_creation_buffer,
                                      width=width + tile_creation_buffer,
                                      crs="EPSG:3857",
                                      transform=transform,
                                      dtype=dtype)

    return cog_profile


def build_cog_from_tiles(file_name,
                         large_tile_object,
                         raster_tile_server_template,
                         desired_small_tile_zoom_level=17,
                         desired_super_res_tile_zoom_level=19,
                         cog=False,
                         indexes=None,
                         tile_size=256
                         ):
    if indexes is None:
        indexes = [1, 2, 3]
        num_channels = len(indexes)
    else:
        num_channels = len(indexes)

    large_cog_profile = create_webmercator_cog_profile(large_tile_object,
                                                       desired_super_res_tile_zoom_level,
                                                       num_channels=num_channels)

    with rasterio.open(file_name, 'w', **large_cog_profile) as dst_dataset:

        small_tile_object_list, small_tile_position_list = tile_generator.create_super_tile_list(large_tile_object,
                                                                                                 desired_zoom_level=desired_small_tile_zoom_level)

        for small_tile_object in tqdm(small_tile_object_list):
            super_res_tile = tile_generator.create_super_tile_image(small_tile_object, raster_tile_server_template,
                                                                    desired_zoom_level=desired_super_res_tile_zoom_level,
                                                                    indexes=indexes,
                                                                    tile_size=tile_size,
                                                                    cog=cog)

            left, bottom, right, top = mercantile.xy_bounds(*small_tile_object)

            dst_window = rasterio.windows.from_bounds(left, bottom, right, top,
                                                      transform=large_cog_profile['transform'])

            dst_dataset.write(super_res_tile.astype(large_cog_profile['dtype']), window=dst_window)


def build_cog_from_tiles_gen(file_name,
                             large_tile_object,
                             raster_tile_server_template,
                             desired_small_tile_zoom_level=17,
                             desired_super_res_tile_zoom_level=19,
                             cog=False,
                             indexes=None,
                             tile_size=256,
                             batch_size=4,
                             num_workers=4
                             ):
    if indexes is None:
        indexes = [1, 2, 3]
        num_channels = len(indexes)
    else:
        num_channels = len(indexes)

    large_cog_profile = create_webmercator_cog_profile(large_tile_object,
                                                       desired_super_res_tile_zoom_level,
                                                       num_channels=num_channels)

    with rasterio.open(file_name, 'w', **large_cog_profile) as dst_dataset:

        tile_dataset = TileClassDataset(root_tile_obj=large_tile_object,
                                        raster_location=raster_tile_server_template,
                                        desired_zoom_level=desired_small_tile_zoom_level,
                                        super_res_zoom_level=desired_super_res_tile_zoom_level,
                                        cog=cog,
                                        tile_size=tile_size,
                                        indexes=indexes
                                        )

        tile_iterator = DataLoader(tile_dataset, batch_size=4,
                                   shuffle=False, num_workers=4)

        for super_res_tile_batch, small_tile_obj_batch in tqdm(tile_iterator):

            for super_res_tile, small_tile_object_tensor in zip(super_res_tile_batch, small_tile_obj_batch):
                left, bottom, right, top = small_tile_object_tensor.numpy()

                dst_window = rasterio.windows.from_bounds(left, bottom, right, top,
                                                          transform=large_cog_profile['transform'])

                dst_dataset.write(super_res_tile.numpy().astype(large_cog_profile['dtype']), window=dst_window)
